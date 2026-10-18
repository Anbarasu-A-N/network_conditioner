#!/usr/bin/env python3
"""
Ubuntu Network Conditioner — Bidirectional
Run with: python3 network_conditioner.py
Requires: iproute2 (sudo apt install iproute2)
          python3-tk  (sudo apt install python3-tk)
"""

import tkinter as tk
from tkinter import ttk, messagebox
import subprocess, sys, os, re, time

# ── Palette ──────────────────────────────────────────────────────────────────
BG       = "#181818"
SURFACE  = "#232323"
SURFACE2 = "#2c2c2c"
BORDER   = "#3a3a3a"
FG       = "#f0f0f0"
FG2      = "#888888"
FG3      = "#4a4a4a"
ACCENT   = "#4a9eff"
GREEN    = "#4caf50"
ORANGE   = "#f0a500"
RED      = "#f44336"
EGRESS_COLOR  = "#4a9eff"   # blue  — outgoing
INGRESS_COLOR = "#a855f7"   # purple — incoming
BOTH_COLOR    = "#22c55e"   # green — both
MONO     = "Courier New"
FONT     = "Inter" if sys.platform != "win32" else "Segoe UI"

THUMB_R  = 9
TRACK_H  = 3
SLIDER_H = 32

PRESETS = [
    ("No limit",     dict(delay=0,   jitter=0,   loss=0,  bw=0,      corrupt=0, dup=0)),
    ("2G",           dict(delay=800, jitter=200, loss=5,  bw=250,    corrupt=0, dup=0)),
    ("3G",           dict(delay=150, jitter=40,  loss=2,  bw=3000,   corrupt=0, dup=0)),
    ("4G",           dict(delay=50,  jitter=15,  loss=1,  bw=20000,  corrupt=0, dup=0)),
    ("Slow WiFi",    dict(delay=30,  jitter=20,  loss=3,  bw=5000,   corrupt=0, dup=0)),
    ("Fast WiFi",    dict(delay=10,  jitter=5,   loss=0,  bw=100000, corrupt=0, dup=0)),
    ("Flaky",        dict(delay=200, jitter=100, loss=15, bw=1000,   corrupt=2, dup=0)),
    ("High latency", dict(delay=300, jitter=0,   loss=0,  bw=0,      corrupt=0, dup=0)),
]

# Direction modes
MODE_EGRESS  = "egress"
MODE_INGRESS = "ingress"
MODE_BOTH    = "both"


# ── Helpers ───────────────────────────────────────────────────────────────────
def get_interfaces():
    try:
        out = subprocess.check_output(["ip", "-o", "link", "show"], text=True)
        ifaces = re.findall(r"\d+:\s+(\S+):", out)
        ifaces = [i for i in ifaces if i != "lo"
                  and not i.startswith("docker")
                  and not i.startswith("br-")
                  and not i.startswith("ifb")]
        return ifaces if ifaces else ["eth0"]
    except Exception:
        return ["eth0"]


def bw_label(v):
    if v == 0:    return "No limit"
    if v >= 1000: return f"{v // 1000} Mbit/s"
    return f"{v} Kbit/s"


def netem_args(delay, jitter, loss, corrupt, dup):
    """Build netem parameter string."""
    parts = []
    if jitter > 0 and delay == 0:
        delay = 1
    if delay > 0:
        p = f"delay {delay}ms"
        if jitter > 0: p += f" {jitter}ms distribution normal"
        parts.append(p)
    if loss    > 0: parts.append(f"loss {loss}%")
    if corrupt > 0: parts.append(f"corrupt {corrupt}%")
    if dup     > 0: parts.append(f"duplicate {dup}%")
    return parts


def build_egress_commands(iface, delay, jitter, loss, bw, corrupt, dup):
    """Build tc commands for outgoing traffic only."""
    lines = ["# ── Egress (outgoing) ─────────────────────────────────────────",
             f"sudo tc qdisc del dev {iface} root 2>/dev/null"]
    netem = netem_args(delay, jitter, loss, corrupt, dup)
    if bw > 0 and netem:
        rate = f"{bw//1000}mbit" if bw >= 1000 else f"{bw}kbit"
        lines += [f"sudo tc qdisc add dev {iface} root handle 1: htb default 10",
                  f"sudo tc class add dev {iface} parent 1: classid 1:10 htb rate {rate}",
                  f"sudo tc qdisc add dev {iface} parent 1:10 handle 10: netem {' '.join(netem)}"]
    elif bw > 0:
        rate = f"{bw//1000}mbit" if bw >= 1000 else f"{bw}kbit"
        lines += [f"sudo tc qdisc add dev {iface} root tbf rate {rate} burst 32kbit latency 400ms"]
    elif netem:
        lines += [f"sudo tc qdisc add dev {iface} root netem {' '.join(netem)}"]
    else:
        lines.append("# No egress conditions — rules cleared")
    return lines


def build_ingress_commands(iface, delay, jitter, loss, bw, corrupt, dup):
    """Build tc commands for incoming traffic via IFB redirect."""
    netem = netem_args(delay, jitter, loss, corrupt, dup)

    if not netem and bw == 0:
        # No conditions — show the exact removal commands that will run
        return [
            "# ── Ingress: No limit — remove all ingress rules ─────────────────",
            f"sudo tc filter del dev {iface} parent ffff:",
            f"sudo tc filter del dev {iface} ingress",
            f"sudo tc qdisc  del dev {iface} ingress",
            f"sudo tc qdisc  del dev ifb0 root",
            f"sudo ip link   set ifb0 down",
            f"sudo ip link   del ifb0",
        ]

    lines = ["# ── Ingress (incoming) via IFB ────────────────────────────────"]
    # IFB setup — done inside apply_ingress(), not as shell commands,
    # so we show them for reference but apply_ingress() handles execution
    lines += [
        "sudo modprobe ifb numifbs=1",
        "sudo modprobe act_mirred",
        "sudo modprobe cls_matchall",
        f"sudo ip link add ifb0 type ifb",
        f"sudo ip link set ifb0 up",
        f"sudo tc qdisc add dev {iface} ingress",
        f"sudo tc filter add dev {iface} parent ffff: matchall "
        f"action mirred egress redirect dev ifb0",
    ]
    if bw > 0 and netem:
        rate = f"{bw//1000}mbit" if bw >= 1000 else f"{bw}kbit"
        lines += [f"sudo tc qdisc add dev ifb0 root handle 1: htb default 10",
                  f"sudo tc class add dev ifb0 parent 1: classid 1:10 htb rate {rate}",
                  f"sudo tc qdisc add dev ifb0 parent 1:10 handle 10: netem {' '.join(netem)}"]
    elif bw > 0:
        rate = f"{bw//1000}mbit" if bw >= 1000 else f"{bw}kbit"
        lines += [f"sudo tc qdisc add dev ifb0 root tbf rate {rate} burst 32kbit latency 400ms"]
    elif netem:
        lines += [f"sudo tc qdisc add dev ifb0 root netem {' '.join(netem)}"]
    return lines


def build_command(iface, delay, jitter, loss, bw, corrupt, dup, mode):
    """Build full command string based on direction mode."""
    if mode == MODE_EGRESS:
        return "\n".join(build_egress_commands(iface, delay, jitter, loss, bw, corrupt, dup))
    elif mode == MODE_INGRESS:
        return "\n".join(build_ingress_commands(iface, delay, jitter, loss, bw, corrupt, dup))
    else:  # BOTH
        eg = build_egress_commands(iface, delay, jitter, loss, bw, corrupt, dup)
        ing = build_ingress_commands(iface, delay, jitter, loss, bw, corrupt, dup)
        return "\n".join(eg + [""] + ing)


def _run(*args):
    """Run a sudo command silently, returning (ok, stderr)."""
    r = subprocess.run(["sudo"] + list(args), capture_output=True, text=True)
    return r.returncode == 0, r.stderr


def _qdisc_show(iface):
    """Return raw tc qdisc show output for an interface (all qdiscs)."""
    r = subprocess.run(["sudo", "tc", "qdisc", "show", "dev", iface],
                       capture_output=True, text=True)
    return r.stdout


def _has_ingress(iface):
    """
    Check both 'ingress' keyword AND 'ffff:' handle — some kernels only
    show 'ffff:' without the word 'ingress' in tc qdisc show output.
    """
    out = _qdisc_show(iface)
    return "ingress" in out or "ffff:" in out


def _nuke_ingress(iface):
    """Try every known form of ingress qdisc deletion."""
    _run("tc", "filter", "del", "dev", iface, "parent", "ffff:")
    _run("tc", "filter", "del", "dev", iface, "ingress")
    time.sleep(0.05)
    _run("tc", "qdisc", "del", "dev", iface, "ingress")
    _run("tc", "qdisc", "del", "dev", iface, "handle", "ffff:", "ingress")
    # Some kernels need the clsact form
    _run("tc", "qdisc", "del", "dev", iface, "clsact")


def _wait_until_clear(iface, timeout=4.0):
    """Poll until ingress/ffff: is gone from qdisc show, up to timeout seconds."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _has_ingress(iface):
            return True
        time.sleep(0.1)
    return False


def force_clear(iface):
    """
    Fully and verifiably tear down ALL tc state on iface and ifb0.
    Uses aggressive retry + polling to guarantee the ingress qdisc is gone
    before returning — preventing the Exclusivity flag error.
    """
    # Phase 1: tear down ifb0 first (removes the mirror target)
    _run("tc", "filter", "del", "dev", "ifb0")
    _run("tc", "qdisc",  "del", "dev", "ifb0", "root")
    _run("ip", "link",   "set", "ifb0", "down")
    _run("ip", "link",   "del", "ifb0")
    time.sleep(0.1)

    # Phase 2: aggressively remove ingress from real interface
    # Try up to 8 times with increasing back-off
    for attempt in range(8):
        if not _has_ingress(iface):
            break
        _nuke_ingress(iface)
        wait = 0.15 * (attempt + 1)   # 0.15, 0.30, 0.45, 0.60, 0.75, 0.90, 1.05, 1.20s
        time.sleep(wait)

    # Phase 3: delete root qdisc
    _run("tc", "qdisc", "del", "dev", iface, "root")
    time.sleep(0.1)

    # Phase 4: final poll — wait up to 4s for kernel to fully release the lock
    if _has_ingress(iface):
        _nuke_ingress(iface)
        _wait_until_clear(iface, timeout=4.0)


def apply_ingress(iface, netem_lines, on_line=None):
    """
    Execute ingress IFB setup step-by-step with kernel verification between stages.
    """
    def do(*args):
        r = subprocess.run(["sudo"] + list(args), capture_output=True, text=True)
        benign = ("already exists", "File exists")
        if r.returncode != 0 and not any(b in r.stderr for b in benign):
            return False, r.stderr.strip()
        return True, ""

    # 1. Load kernel modules
    for mod in ("ifb", "act_mirred", "cls_matchall"):
        arg = ["numifbs=1"] if mod == "ifb" else []
        subprocess.run(["sudo", "modprobe", mod] + arg, capture_output=True)
    time.sleep(0.15)

    # 2. Create ifb0 — if it already exists from a stale state, delete and recreate
    _run("ip", "link", "set", "ifb0", "down")
    _run("ip", "link", "del", "ifb0")
    time.sleep(0.1)
    ok, err = do("ip", "link", "add", "ifb0", "type", "ifb")
    if not ok: return False, f"ip link add ifb0: {err}"

    # 3. Bring ifb0 up and confirm
    ok, err = do("ip", "link", "set", "ifb0", "up")
    if not ok: return False, f"ip link set ifb0 up: {err}"

    # Poll until ifb0 is operationally up
    for _ in range(10):
        r = subprocess.run(["ip", "link", "show", "ifb0"], capture_output=True, text=True)
        if "UP" in r.stdout or "UNKNOWN" in r.stdout:
            break
        time.sleep(0.1)

    # 4. Confirm ingress qdisc is gone before adding it
    if _has_ingress(iface):
        _nuke_ingress(iface)
        if not _wait_until_clear(iface, timeout=5.0):
            return False, (
                f"Could not remove existing ingress qdisc on {iface} after 5s.\n"
                f"Run this manually first:\n"
                f"  sudo tc qdisc del dev {iface} ingress\n"
                f"  sudo tc qdisc del dev {iface} handle ffff: ingress\n"
                f"Then try Apply again.")

    # 5. Add ingress qdisc
    ok, err = do("tc", "qdisc", "add", "dev", iface, "ingress")
    if not ok: return False, f"tc qdisc add ingress: {err}"
    time.sleep(0.15)

    # 6. Add mirror filter to redirect ingress → ifb0
    ok, err = do("tc", "filter", "add", "dev", iface,
                 "parent", "ffff:", "matchall",
                 "action", "mirred", "egress", "redirect", "dev", "ifb0")
    if not ok: return False, f"tc filter add matchall mirred: {err}"
    time.sleep(0.1)

    # 7. Apply netem / htb rules to ifb0
    SKIP_KEYS = ("modprobe", "ip link", f"qdisc add dev {iface} ingress",
                 "filter add", "qdisc del", "link del", "link set ifb0 down")
    for line in netem_lines:
        line = line.strip()
        if not line or line.startswith("#"): continue
        if any(k in line for k in SKIP_KEYS): continue
        if on_line: on_line(line, "running")
        cmd = line.replace("sudo ", "").split()
        ok, err = do(*cmd)
        if not ok:
            if on_line: on_line(line, "error")
            return False, f"{' '.join(cmd)}: {err}"
        if on_line: on_line(line, "ok")

    return True, ""


def run_tc(commands, iface, mode=None, on_line=None):
    """
    on_line(line, state): callback for UI highlighting.
      state = 'running' | 'ok' | 'error'
    """
    force_clear(iface)

    # Detect "No limit" clear-only commands
    active = [l.strip() for l in commands.splitlines()
              if l.strip() and not l.strip().startswith("#")]
    clear_only_keys = ("filter del", "qdisc  del", "link   set", "link   del",
                        "qdisc del", "link set", "link del")
    if not active or all(any(k in l for k in clear_only_keys) for l in active):
        return True, "cleared"

    # Ingress: careful step-by-step
    if mode in (MODE_INGRESS, MODE_BOTH):
        ok, err = apply_ingress(iface, commands.splitlines(), on_line=on_line)
        if not ok: return False, err
        if mode == MODE_INGRESS:
            return True, ""

    # Egress: run commands directly with per-line highlighting
    SKIP = ("qdisc del", "link del ifb0", "link set ifb0 down", "filter del",
            "modprobe", "ip link add ifb0", "ip link set ifb0",
            "qdisc add dev " + iface + " ingress",
            "filter add dev " + iface)
    BENIGN = ("Cannot find device", "No such", "already exists",
              "File exists", "Exclusivity flag", "Cannot delete")
    for line in commands.splitlines():
        line = line.strip()
        if not line or line.startswith("#"): continue
        if any(p in line for p in SKIP): continue
        if "ifb0" in line: continue
        if on_line: on_line(line, "running")
        cmd = line.replace("sudo ", "")
        try:
            r = subprocess.run(["sudo"] + cmd.split(), capture_output=True, text=True)
            if r.returncode != 0 and not any(b in r.stderr for b in BENIGN):
                if on_line: on_line(line, "error")
                return False, r.stderr
            if on_line: on_line(line, "ok")
        except Exception as e:
            if on_line: on_line(line, "error")
            return False, str(e)
    return True, ""


# ── Canvas helpers ────────────────────────────────────────────────────────────
def rounded_rect_points(x1, y1, x2, y2, r):
    return [x1+r,y1, x2-r,y1, x2,y1, x2,y1+r,
            x2,y2-r, x2,y2, x2-r,y2, x1+r,y2,
            x1,y2, x1,y2-r, x1,y1+r, x1,y1]


class RoundedFrame(tk.Canvas):
    def __init__(self, parent, radius=12, fill=SURFACE, outline=BORDER,
                 outline_width=1, **kw):
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("bd", 0)
        super().__init__(parent, bg=BG, **kw)
        self._r, self._fill = radius, fill
        self._outline, self._ow = outline, outline_width
        self._shape = None
        self.inner = tk.Frame(self, bg=fill)
        self._win  = self.create_window(0, 0, window=self.inner, anchor="nw")
        self.bind("<Configure>", self._on_resize)

    def _on_resize(self, e=None):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4: return
        r = min(self._r, w//2, h//2)
        if self._shape: self.delete(self._shape)
        pts = rounded_rect_points(1, 1, w-1, h-1, r)
        self._shape = self.create_polygon(pts, smooth=True,
                                          fill=self._fill,
                                          outline=self._outline,
                                          width=self._ow)
        self.tag_lower(self._shape)
        pad = max(r // 2, 8)
        self.itemconfig(self._win, width=w - pad*2, height=h - pad*2)
        self.coords(self._win, pad, pad)

    def set_outline(self, color):
        self._outline = color
        if self._shape:
            self.itemconfig(self._shape, outline=color)


# ── Direction Toggle ──────────────────────────────────────────────────────────
class DirectionToggle(tk.Canvas):
    """
    3-segment pill toggle: [↑ Egress] [⇅ Both] [↓ Ingress]
    Each segment lights up in its own accent color.
    """
    MODES = [
        (MODE_EGRESS,  "↑  Egress",  EGRESS_COLOR),
        (MODE_BOTH,    "⇅  Both",    BOTH_COLOR),
        (MODE_INGRESS, "↓  Ingress", INGRESS_COLOR),
    ]

    GAP = 6   # px gap between each pill segment

    def __init__(self, parent, callback, **kw):
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("bd", 0)
        super().__init__(parent, bg=BG, **kw)
        self._callback = callback
        self._mode     = MODE_EGRESS
        self._font     = (FONT, 10, "bold")
        self._segs     = []

        # Measure the widest label to size each segment equally
        max_tw = 0
        for _, label, _ in self.MODES:
            tmp = tk.Label(parent, text=label, font=self._font)
            tmp.update_idletasks()
            max_tw = max(max_tw, tmp.winfo_reqwidth())
            tmp.destroy()

        self._sw = max_tw + 32          # segment width with padding
        tmp2 = tk.Label(parent, text="↑  Egress", font=self._font)
        tmp2.update_idletasks()
        H = tmp2.winfo_reqheight() + 18
        tmp2.destroy()

        W = self._sw * 3 + self.GAP * 2 + 4
        self.config(width=W, height=H)
        self._W, self._H = W, H

        self._draw()
        self.bind("<ButtonRelease-1>", self._on_click)
        self.bind("<Motion>",          self._on_hover)
        self.bind("<Leave>",           self._on_leave)

    def _draw(self):
        self.delete("all")
        H   = self._H
        sw  = self._sw
        gap = self.GAP
        r   = H // 2
        self._segs = []

        for i, (mode, label, color) in enumerate(self.MODES):
            # Each segment is a fully independent pill with gap between them
            x1 = i * (sw + gap) + 2
            x2 = x1 + sw
            y1, y2 = 1, H - 1
            active = (mode == self._mode)

            fill    = color  if active else SURFACE2
            outline = color  if active else BORDER
            fg      = FG     if active else FG2

            # Full pill shape for every segment
            pts = rounded_rect_points(x1, y1, x2, y2, r)
            shape = self.create_polygon(pts, smooth=True,
                                        fill=fill, outline=outline, width=1)

            text = self.create_text((x1 + x2) // 2, H // 2, text=label,
                                    fill=fg, font=self._font)
            self._segs.append((x1, x2, mode, shape, text, color))

    def _seg_at(self, x):
        for x1, x2, mode, shape, text, color in self._segs:
            if x1 <= x <= x2:
                return mode
        return None

    def _on_click(self, e):
        m = self._seg_at(e.x)
        if m and m != self._mode:
            self._mode = m
            self._draw()
            self._callback(m)

    def _on_hover(self, e):
        m = self._seg_at(e.x)
        for x1, x2, mode, shape, text, color in self._segs:
            if mode == self._mode: continue
            if mode == m:
                self.itemconfig(shape, fill=SURFACE)
            else:
                self.itemconfig(shape, fill=SURFACE2)

    def _on_leave(self, _):
        for x1, x2, mode, shape, text, color in self._segs:
            if mode != self._mode:
                self.itemconfig(shape, fill=SURFACE2)

    def get(self): return self._mode

    def set(self, mode):
        self._mode = mode
        self._draw()


# ── Direction badge (shown under toggle) ─────────────────────────────────────
class DirectionBadge(tk.Canvas):
    """Small colored pill showing current mode with an icon and description."""
    def __init__(self, parent, **kw):
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("bd", 0)
        super().__init__(parent, bg=BG, **kw)
        self._font = (FONT, 9)
        self._text_id = None
        self._shape   = None
        self.config(width=220, height=22)
        self._set("egress")

    def _tint(self, hex_color, alpha=0.15):
        """Blend hex_color toward BG. Returns a valid #rrggbb tkinter color."""
        r  = int(hex_color[1:3], 16); g  = int(hex_color[3:5], 16); b  = int(hex_color[5:7], 16)
        br = int(BG[1:3], 16);        bg_ = int(BG[3:5], 16);       bb = int(BG[5:7], 16)
        return "#{:02x}{:02x}{:02x}".format(
            int(br + (r - br) * alpha),
            int(bg_ + (g - bg_) * alpha),
            int(bb + (b - bb) * alpha))

    def _set(self, mode):
        self.delete("all")
        if mode == MODE_EGRESS:
            color = EGRESS_COLOR
            msg   = "↑  Outgoing traffic only"
        elif mode == MODE_INGRESS:
            color = INGRESS_COLOR
            msg   = "↓  Incoming traffic only"
        else:
            color = BOTH_COLOR
            msg   = "⇅  Both directions (uses IFB)"

        W, H = 220, 22
        r = H // 2
        pts = rounded_rect_points(1, 1, W-1, H-1, r)
        self.create_polygon(pts, smooth=True,
                            fill=self._tint(color, 0.15),
                            outline=color, width=1)
        self.create_text(W//2, H//2, text=msg, fill=color,
                         font=self._font)

    def update_mode(self, mode):
        self._set(mode)


# ── Slider ────────────────────────────────────────────────────────────────────
class Slider(tk.Frame):
    def __init__(self, parent, label, from_, to, fmt, callback, **kw):
        super().__init__(parent, bg=BG, **kw)
        self.fmt = fmt; self.callback = callback
        self._min = from_; self._max = to; self._val = from_
        self._dragging = False

        top = tk.Frame(self, bg=BG)
        top.pack(fill="x")
        tk.Label(top, text=label, bg=BG, fg=FG2,
                 font=(FONT, 10)).pack(side="left")
        self.val_lbl = tk.Label(top, text=fmt(from_), bg=BG, fg=FG,
                                font=(FONT, 10, "bold"))
        self.val_lbl.pack(side="right")

        self.cv = tk.Canvas(self, bg=BG, height=SLIDER_H,
                            highlightthickness=0, bd=0)
        self.cv.pack(fill="x", pady=(4, 0))
        self.cv.bind("<Configure>",       self._draw)
        self.cv.bind("<ButtonPress-1>",   self._on_press)
        self.cv.bind("<B1-Motion>",       self._on_drag)
        self.cv.bind("<ButtonRelease-1>", self._on_release)
        self._accent = EGRESS_COLOR

    def set_accent(self, color):
        self._accent = color
        self._draw()

    def _track_x_range(self):
        w = self.cv.winfo_width()
        return THUMB_R + 2, w - THUMB_R - 2

    def _val_to_x(self, v):
        x1, x2 = self._track_x_range()
        ratio = (v - self._min) / max(self._max - self._min, 1)
        return x1 + ratio * (x2 - x1)

    def _x_to_val(self, x):
        x1, x2 = self._track_x_range()
        ratio = max(0.0, min(1.0, (x - x1) / max(x2 - x1, 1)))
        return int(self._min + ratio * (self._max - self._min))

    def _draw(self, _=None):
        cv = self.cv
        w, h = cv.winfo_width(), cv.winfo_height()
        if w < 4: return
        cy = h // 2
        x1, x2 = self._track_x_range()
        tx = self._val_to_x(self._val)
        cv.delete("all")
        cv.create_line(x1, cy, x2, cy, fill=BORDER, width=TRACK_H, capstyle="round")
        cv.create_line(x1, cy, max(x1, tx), cy, fill=self._accent,
                       width=TRACK_H, capstyle="round")
        gr = THUMB_R + 4
        cv.create_oval(tx-gr, cy-gr, tx+gr, cy+gr, fill="#2a2a2a", outline="")
        cv.create_oval(tx-THUMB_R, cy-THUMB_R, tx+THUMB_R, cy+THUMB_R,
                       fill=FG, outline=FG2, width=1)

    def _on_press(self, e):   self._dragging = True;  self._set_from_x(e.x)
    def _on_drag(self, e):
        if self._dragging: self._set_from_x(e.x)
    def _on_release(self, e): self._dragging = False; self._set_from_x(e.x)

    def _set_from_x(self, x):
        v = self._x_to_val(x)
        self._val = v
        self.val_lbl.config(text=self.fmt(v))
        self._draw()
        self.callback()

    def get(self):    return self._val
    def set(self, v):
        self._val = int(v)
        self.val_lbl.config(text=self.fmt(self._val))
        self._draw()


# ── Pill button ───────────────────────────────────────────────────────────────
class PillButton(tk.Canvas):
    def __init__(self, parent, text, command=None,
                 bg=SURFACE, fg=FG, border=BORDER,
                 active_bg=SURFACE2, active_border=ACCENT,
                 font_size=10, padx=18, pady=8, **kw):
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("bd", 0)
        super().__init__(parent, bg=BG, cursor="hand2", **kw)
        self._command = command
        self._bg, self._fg = bg, fg
        self._border = border
        self._active_bg, self._active_bdr = active_bg, active_border
        self._font   = (FONT, font_size)
        self._active = False

        tmp = tk.Label(parent, text=text, font=self._font)
        tmp.update_idletasks()
        W = tmp.winfo_reqwidth() + padx * 2
        H = tmp.winfo_reqheight() + pady * 2
        tmp.destroy()
        self.config(width=W, height=H)

        r = H // 2
        pts = rounded_rect_points(1, 1, W-1, H-1, r)
        self._shape = self.create_polygon(pts, smooth=True,
                                          fill=bg, outline=border, width=1)
        self._label = self.create_text(W//2, H//2, text=text,
                                       fill=fg, font=self._font)
        self.bind("<ButtonPress-1>",   lambda _: self.itemconfig(self._shape, fill=SURFACE2))
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>",  lambda _: self.itemconfig(self._shape, fill=SURFACE2) if not self._active else None)
        self.bind("<Leave>",  lambda _: self.itemconfig(self._shape, fill=self._active_bg if self._active else self._bg))

    def _on_release(self, _):
        self.itemconfig(self._shape, fill=self._active_bg if self._active else self._bg)
        if self._command: self._command()

    def set_active(self, state):
        self._active = state
        self.itemconfig(self._shape,
                        fill=self._active_bg if state else self._bg,
                        outline=self._active_bdr if state else self._border)

    def set_accent(self, color):
        self._active_bdr = color
        if self._active:
            self.itemconfig(self._shape, outline=color)


# ── Rounded action button ─────────────────────────────────────────────────────
class RoundedButton(tk.Canvas):
    def __init__(self, parent, text, command=None,
                 accent=False, accent_color=None,
                 padx=20, pady=10, **kw):
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("bd", 0)
        super().__init__(parent, bg=BG, cursor="hand2", **kw)
        self._command  = command
        c = accent_color or ACCENT
        self._bg       = c if accent else SURFACE
        self._hover_bg = self._darken(c) if accent else SURFACE2
        self._fg       = "white" if accent else FG
        self._border   = c if accent else BORDER
        self._font     = (FONT, 10, "bold" if accent else "normal")
        self._accent   = accent
        self._color    = c

        tmp = tk.Label(parent, text=text, font=self._font)
        tmp.update_idletasks()
        W = tmp.winfo_reqwidth() + padx * 2
        H = tmp.winfo_reqheight() + pady * 2
        tmp.destroy()
        self.config(width=W, height=H)

        r = 8
        pts = rounded_rect_points(1, 1, W-1, H-1, r)
        self._shape = self.create_polygon(pts, smooth=True,
                                          fill=self._bg,
                                          outline=self._border, width=1)
        self._label = self.create_text(W//2, H//2, text=text,
                                       fill=self._fg, font=self._font)
        self.bind("<ButtonPress-1>",   lambda _: self.itemconfig(self._shape, fill=self._hover_bg))
        self.bind("<ButtonRelease-1>", lambda _: (self.itemconfig(self._shape, fill=self._bg),
                                                   self._command() if self._command else None))
        self.bind("<Enter>",  lambda _: self.itemconfig(self._shape, fill=self._hover_bg))
        self.bind("<Leave>",  lambda _: self.itemconfig(self._shape, fill=self._bg))

    def _darken(self, hex_color):
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
        return f"#{max(0,r-30):02x}{max(0,g-30):02x}{max(0,b-30):02x}"

    def set_text(self, t): self.itemconfig(self._label, text=t)

    def set_accent_color(self, color):
        if self._accent:
            self._bg = color
            self._hover_bg = self._darken(color)
            self._border   = color
            self._color    = color
            self.itemconfig(self._shape, fill=self._bg, outline=self._border)


# ── Rounded text box ──────────────────────────────────────────────────────────
class RoundedText(tk.Frame):
    def __init__(self, parent, height=7, **kw):
        super().__init__(parent, bg=BG, **kw)
        # Convert line-height to pixels: Courier New 10pt ≈ 16px per line + padding
        px_height = height * 16 + 20
        self.config(height=px_height)
        self._rf = RoundedFrame(self, radius=12, fill=SURFACE, outline=BORDER)
        self._rf.pack(fill="both", expand=True)
        self.text = tk.Text(
            self._rf.inner, bg=SURFACE, fg=FG, font=(MONO, 10),
            relief="flat", bd=0, height=height, wrap="word",
            state="disabled", selectbackground=SURFACE2,
            insertbackground=FG, cursor="arrow"
        )
        self.text.tag_configure("comment",  foreground=FG3)
        self.text.tag_configure("section",  foreground=FG2, font=(MONO, 9))
        self.text.tag_configure("running",  foreground="#000000", background="#f0a500",
                                font=(MONO, 10, "bold"))
        self.text.tag_configure("done_ok",  foreground="#4caf50")
        self.text.tag_configure("done_err", foreground="#f44336")
        self._lines = []
        self._highlighted = -1
        self.text.pack(fill="both", expand=True)

    def set_text(self, content):
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self._lines = content.splitlines()
        for line in self._lines:
            if line.startswith("# ──"):
                self.text.insert("end", line + "\n", "section")
            elif line.startswith("#"):
                self.text.insert("end", line + "\n", "comment")
            else:
                self.text.insert("end", line + "\n")
        self.text.config(state="disabled")

    def _norm(self, s):
        return s.strip().replace("sudo ", "")

    def highlight_line(self, line_text):
        """Highlight the matching command line as currently executing."""
        self.text.config(state="normal")
        self.text.tag_remove("running", "1.0", "end")
        for i, line in enumerate(getattr(self, "_lines", [])):
            if not line.startswith("#") and self._norm(line) == self._norm(line_text):
                self.text.tag_add("running", f"{i+1}.0", f"{i+2}.0")
                self.text.see(f"{i+1}.0")
                break
        self.text.config(state="disabled")

    def mark_done(self, line_text, success=True):
        """Color a command line green (ok) or red (error) after it runs."""
        self.text.config(state="normal")
        tag = "done_ok" if success else "done_err"
        for i, line in enumerate(getattr(self, "_lines", [])):
            if not line.startswith("#") and self._norm(line) == self._norm(line_text):
                self.text.tag_remove("running",  f"{i+1}.0", f"{i+2}.0")
                self.text.tag_add(tag,           f"{i+1}.0", f"{i+2}.0")
                break
        self.text.config(state="disabled")

    def clear_highlights(self):
        self.text.config(state="normal")
        for tag in ("running", "done_ok", "done_err"):
            self.text.tag_remove(tag, "1.0", "end")
        self.text.config(state="disabled")

    def set_border_color(self, color):
        self._rf.set_outline(color)


# ── Main App ──────────────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Network Conditioner")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.minsize(480, 600)

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Dark.TCombobox",
                        fieldbackground=SURFACE2, background=SURFACE2,
                        foreground=FG, selectforeground=FG,
                        selectbackground=SURFACE2, bordercolor=BORDER,
                        arrowcolor=FG, arrowsize=14, relief="flat",
                        padding=(14, 14))
        style.map("Dark.TCombobox",
                  fieldbackground=[("readonly", SURFACE2), ("focus", SURFACE2), ("active", SURFACE2)],
                  foreground=[("readonly", FG), ("focus", FG), ("active", FG)],
                  selectforeground=[("readonly", FG), ("focus", FG)],
                  selectbackground=[("readonly", SURFACE2), ("focus", SURFACE2)],
                  background=[("readonly", SURFACE2), ("active", SURFACE2)])

        self.interfaces   = get_interfaces()
        self._current_cmd = ""
        self._mode        = MODE_EGRESS
        self._build_ui()
        self.after(50, lambda: self._on_mode_change(MODE_EGRESS))
        self._update()

    def _accent_color(self):
        return {MODE_EGRESS: EGRESS_COLOR,
                MODE_INGRESS: INGRESS_COLOR,
                MODE_BOTH: BOTH_COLOR}[self._mode]

    def _build_ui(self):
        wrap = tk.Frame(self, bg=BG)
        wrap.pack(fill="both", expand=True, padx=16, pady=12)

        # ── Top row: Interface + Direction Toggle ─────────────────────────────
        top_row = tk.Frame(wrap, bg=BG)
        top_row.pack(fill="x", pady=(0, 14))

        tk.Label(top_row, text="Interface", bg=BG, fg=FG2,
                 font=(FONT, 11)).pack(side="left", padx=(0, 14))

        self.iface_var = tk.StringVar(value=self.interfaces[0])
        cb_outer = tk.Frame(top_row, bg=BORDER, padx=1, pady=1)
        cb_outer.pack(side="left", fill="x", expand=True, padx=(0, 16))
        cb_inner = tk.Frame(cb_outer, bg=SURFACE2)
        cb_inner.pack(fill="both", expand=True)
        cb = ttk.Combobox(cb_inner, textvariable=self.iface_var,
                          values=self.interfaces, state="readonly",
                          font=(FONT, 12), style="Dark.TCombobox")
        cb.pack(fill="both", expand=True)
        cb.bind("<<ComboboxSelected>>", lambda _: self._update())
        cb.bind("<<ComboboxSelected>>", lambda e: (self._update(), cb.selection_clear()), add=True)

        # Direction toggle — top right
        toggle_wrap = tk.Frame(top_row, bg=BG)
        toggle_wrap.pack(side="right")

        self.direction_toggle = DirectionToggle(toggle_wrap, callback=self._on_mode_change)
        self.direction_toggle.pack()

        self.direction_badge = DirectionBadge(toggle_wrap)
        self.direction_badge.pack(pady=(6, 0))

        # ── Presets ──────────────────────────────────────────────────────────
        tk.Label(wrap, text="PRESETS", bg=BG, fg=FG2,
                 font=(FONT, 9)).pack(anchor="w", pady=(0, 8))
        prow = tk.Frame(wrap, bg=BG)
        prow.pack(fill="x", pady=(0, 18))
        self.preset_btns = []
        # Use grid with wrap: pack into rows of 4 to handle narrow windows
        for i, (name, vals) in enumerate(PRESETS):
            btn = PillButton(prow, text=name,
                             command=lambda v=vals, n=name: self._apply_preset(n, v),
                             font_size=10, padx=16, pady=8,
                             active_border=self._accent_color())
            btn.grid(row=i//4, column=i%4, padx=(0,8), pady=(0,6), sticky="w")
            self.preset_btns.append((name, btn))

        # ── Sliders ───────────────────────────────────────────────────────────
        sg = tk.Frame(wrap, bg=BG)
        sg.pack(fill="x", pady=(0, 16))
        sg.columnconfigure(0, weight=1, uniform="col")
        sg.columnconfigure(1, weight=1, uniform="col")
        tk.Frame(sg, bg=BORDER, width=1).grid(row=0, column=2, rowspan=3,
                                               sticky="ns", padx=8)

        def ms(v):  return f"{v} ms"
        def pct(v): return f"{v}%"

        sliders_def = [
            ("delay",   "Latency (delay)", 0, 2000,   ms,       0, 0),
            ("jitter",  "Jitter",          0, 500,    ms,       0, 1),
            ("loss",    "Packet loss",     0, 100,    pct,      1, 0),
            ("bw",      "Bandwidth limit", 0, 100000, bw_label, 1, 1),
            ("corrupt", "Corruption",      0, 100,    pct,      2, 0),
            ("dup",     "Duplication",     0, 100,    pct,      2, 1),
        ]
        self.sliders = {}
        for key, label, lo, hi, fmt, row, col in sliders_def:
            s = Slider(sg, label, lo, hi, fmt, self._update)
            s.set_accent(self._accent_color())
            px = (0, 20) if col == 0 else (20, 0)
            s.grid(row=row, column=col, sticky="ew", padx=px, pady=(0, 14))
            self.sliders[key] = s

        # ── Stat cards — responsive 2-col grid that reflows on small screens ──
        crow = tk.Frame(wrap, bg=BG)
        crow.pack(fill="x", pady=(0, 14))
        crow.columnconfigure(0, weight=1, uniform="card")
        crow.columnconfigure(1, weight=1, uniform="card")
        self.stat_cards = {}
        self.stat_vars  = {}
        CARD_DEFS = [("DELAY","delay"),("PACKET LOSS","loss"),
                     ("BANDWIDTH","bw"),("JITTER","jitter")]
        for idx, (label, key) in enumerate(CARD_DEFS):
            row = idx // 2
            col = idx % 2
            pad_x = (0, 6) if col == 0 else (6, 0)
            card = RoundedFrame(crow, radius=12, fill=BG, outline=BORDER,
                                outline_width=1, height=88)
            card.grid(row=row, column=col, sticky="nsew",
                      padx=pad_x, pady=(0, 10))
            card.inner.config(bg=BG)
            center = tk.Frame(card.inner, bg=BG)
            center.place(relx=0.5, rely=0.5, anchor="center")
            tk.Label(center, text=label, bg=BG, fg=FG2,
                     font=(FONT, 9)).pack(anchor="center")
            sv = tk.StringVar(value="0 ms" if key in ("delay","jitter")
                              else ("No limit" if key=="bw" else "0 %"))
            tk.Label(center, textvariable=sv, bg=BG, fg=FG,
                     font=(FONT, 22, "bold")).pack(anchor="center", pady=(2,0))
            self.stat_vars[key]  = sv
            self.stat_cards[key] = card

        # ── Command box ───────────────────────────────────────────────────────
        self.cmd_box = RoundedText(wrap, height=18)
        self.cmd_box.pack(fill="x", pady=(0, 14))
        self.cmd_box.pack_propagate(False)  # lock the pixel height we set

        # ── Buttons ───────────────────────────────────────────────────────────
        brow = tk.Frame(wrap, bg=BG)
        brow.pack(fill="x")

        self.copy_btn = RoundedButton(brow, text="⎘  Copy command",
                                      command=self._copy, padx=20, pady=10)
        self.copy_btn.pack(side="left", padx=(0, 10))

        self.apply_btn = RoundedButton(brow, text="▶  Apply to system",
                                       command=self._apply, accent=True,
                                       accent_color=self._accent_color(),
                                       padx=20, pady=10)
        self.apply_btn.pack(side="left", padx=(0, 10))

        self.reset_btn = RoundedButton(brow, text="Reset",
                                       command=self._reset, padx=20, pady=10)
        self.reset_btn.pack(side="left")

        self.status_lbl = tk.Label(brow, text="", bg=BG, fg=FG2, font=(FONT, 9))
        self.status_lbl.pack(side="left", padx=14)

    # ── Mode change ───────────────────────────────────────────────────────────
    def _on_mode_change(self, mode):
        self._mode = mode
        color = self._accent_color()
        # Update slider track colors
        for s in self.sliders.values():
            s.set_accent(color)
        # Update preset button active borders
        for n, btn in self.preset_btns:
            btn.set_accent(color)
        # Update apply button color
        self.apply_btn.set_accent_color(color)
        # Update command box border
        self.cmd_box.set_border_color(color)
        # Update stat card borders
        for card in self.stat_cards.values():
            card.set_outline(color)
        # Update badge
        self.direction_badge.update_mode(mode)
        self._update()

    # ── Logic ─────────────────────────────────────────────────────────────────
    def _update(self, _=None):
        iface   = self.iface_var.get()
        delay   = self.sliders["delay"].get()
        jitter  = self.sliders["jitter"].get()
        loss    = self.sliders["loss"].get()
        bw      = self.sliders["bw"].get()
        corrupt = self.sliders["corrupt"].get()
        dup     = self.sliders["dup"].get()

        # Jitter warning
        jitter_label = f"{jitter} ms"
        if jitter > 0 and delay == 0:
            jitter_label = f"{jitter} ms ⚠ needs delay"
        self.sliders["jitter"].val_lbl.config(
            text=jitter_label,
            fg=ORANGE if (jitter > 0 and delay == 0) else FG)

        self.stat_vars["delay"].set(f"{delay} ms")
        self.stat_vars["loss"].set(f"{loss} %")
        self.stat_vars["bw"].set(bw_label(bw))
        self.stat_vars["jitter"].set(f"{jitter} ms" if delay > 0 or jitter == 0
                                     else f"{jitter} ms*")

        self._current_cmd = build_command(iface, delay, jitter, loss, bw,
                                          corrupt, dup, self._mode)
        self.cmd_box.set_text(self._current_cmd)

    def _apply_preset(self, name, vals):
        for n, btn in self.preset_btns: btn.set_active(n == name)
        for key, val in vals.items():   self.sliders[key].set(val)
        self._update()

    def _copy(self):
        self.clipboard_clear()
        self.clipboard_append(self._current_cmd)
        self.copy_btn.set_text("✓  Copied!")
        self.after(2000, lambda: self.copy_btn.set_text("⎘  Copy command"))

    def _apply(self):
        if os.geteuid() != 0:
            if not messagebox.askyesno("Sudo required",
                "Applying tc rules requires sudo.\n\nProceed?"): return
        self.status_lbl.config(text="")
        self.apply_btn.set_text("Applying…")
        self.cmd_box.clear_highlights()
        self.update()

        # Callback: highlight each line as it executes, green/red when done
        def on_line(line, state):
            if state == "running":
                self.cmd_box.highlight_line(line)
            elif state == "ok":
                self.cmd_box.mark_done(line, success=True)
            elif state == "error":
                self.cmd_box.mark_done(line, success=False)
            self.update_idletasks()

        ok, sentinel = run_tc(self._current_cmd, self.iface_var.get(),
                               self._mode, on_line=on_line)
        self.apply_btn.set_text("▶  Apply to system")
        if ok:
            label = "✓ Cleared" if sentinel == "cleared" else "✓ Applied"
            self.status_lbl.config(text=label, fg=GREEN)
            self.after(4000, lambda: self.status_lbl.config(text=""))
        else:
            self.status_lbl.config(text="✗ Error", fg=RED)
            messagebox.showerror("Apply failed", f"tc returned:\n\n{sentinel}")

    def _reset(self):
        for n, btn in self.preset_btns: btn.set_active(False)
        for s in self.sliders.values(): s.set(0)
        self._update()
        iface = self.iface_var.get()
        force_clear(iface)
        self.status_lbl.config(text="✓ Reset", fg=GREEN)
        self.after(4000, lambda: self.status_lbl.config(text=""))


if __name__ == "__main__":
    app = App()
    app.mainloop()