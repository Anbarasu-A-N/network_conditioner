#!/usr/bin/env python3
"""
Ubuntu Network Conditioner
Run with: python3 network_conditioner.py
Requires: iproute2 (sudo apt install iproute2)

# 1. Update package list
sudo apt update

# 2. Install iproute2 (provides the tc command)
sudo apt install -y iproute2

# 3. Install Python tkinter (the GUI library)
sudo apt install -y python3-tk

# 4. Verify everything is installed correctly
tc -version
python3 -c "import tkinter; print('tkinter OK')"
"""

import tkinter as tk
from tkinter import ttk, messagebox
import subprocess, sys, os, re

# ── Palette ──────────────────────────────────────────────────────────────────
BG       = "#181818"
SURFACE  = "#232323"
SURFACE2 = "#2c2c2c"
BORDER   = "#3a3a3a"
FG       = "#f0f0f0"
FG2      = "#888888"
FG3      = "#4a4a4a"
ACCENT   = "#4a9eff"
MONO     = "Courier New"
FONT     = "Inter" if sys.platform != "win32" else "Segoe UI"

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

THUMB_R  = 9    # radius of the round slider thumb
TRACK_H  = 3    # height of the track line
SLIDER_H = 32   # total canvas height per slider


def get_interfaces():
    try:
        out = subprocess.check_output(["ip", "-o", "link", "show"], text=True)
        ifaces = re.findall(r"\d+:\s+(\S+):", out)
        ifaces = [i for i in ifaces if i != "lo"
                  and not i.startswith("docker")
                  and not i.startswith("br-")]
        return ifaces if ifaces else ["eth0"]
    except Exception:
        return ["eth0"]


def bw_label(v):
    if v == 0:    return "No limit"
    if v >= 1000: return f"{v // 1000} Mbit/s"
    return f"{v} Kbit/s"


def build_command(iface, delay, jitter, loss, bw, corrupt, dup):
    lines = ["# Remove any existing rules",
             f"sudo tc qdisc del dev {iface} root 2>/dev/null"]
    netem = []
    # jitter requires a base delay — auto-set 1ms minimum so jitter is never silently dropped
    if jitter > 0 and delay == 0:
        delay = 1
    if delay > 0:
        p = f"delay {delay}ms"
        if jitter > 0: p += f" {jitter}ms distribution normal"
        netem.append(p)
    if loss    > 0: netem.append(f"loss {loss}%")
    if corrupt > 0: netem.append(f"corrupt {corrupt}%")
    if dup     > 0: netem.append(f"duplicate {dup}%")
    if bw > 0 and netem:
        rate = f"{bw//1000}mbit" if bw >= 1000 else f"{bw}kbit"
        lines += ["\n# Add bandwidth limit (HTB) + netem conditions",
                  f"sudo tc qdisc add dev {iface} root handle 1: htb default 10",
                  f"sudo tc class add dev {iface} parent 1: classid 1:10 htb rate {rate}",
                  f"sudo tc qdisc add dev {iface} parent 1:10 handle 10: netem {' '.join(netem)}"]
    elif bw > 0:
        rate = f"{bw//1000}mbit" if bw >= 1000 else f"{bw}kbit"
        lines += ["\n# Add bandwidth limit only",
                  f"sudo tc qdisc add dev {iface} root tbf rate {rate} burst 32kbit latency 400ms"]
    elif netem:
        lines += ["\n# Apply network conditions",
                  f"sudo tc qdisc add dev {iface} root netem {' '.join(netem)}"]
    else:
        lines.append("\n# No conditions — rules cleared")
    return "\n".join(lines)


def force_clear(iface):
    """Aggressively remove ALL qdiscs from interface before applying new rules."""
    # Try deleting root qdisc (covers htb, netem, tbf, etc.)
    subprocess.run(["sudo", "tc", "qdisc", "del", "dev", iface, "root"],
                   capture_output=True)
    # Also try ingress
    subprocess.run(["sudo", "tc", "qdisc", "del", "dev", iface, "ingress"],
                   capture_output=True)
    import time; time.sleep(0.15)  # small pause to let kernel release the lock


def run_tc(commands):
    # First pass: find the interface and force-clear it
    for line in commands.splitlines():
        line = line.strip()
        if not line or line.startswith("#"): continue
        parts = line.replace("sudo ", "").split()
        if "tc" in parts and "qdisc" in parts and "del" in parts:
            try:
                idx = parts.index("dev") + 1
                force_clear(parts[idx])
            except (ValueError, IndexError):
                pass
            break  # only need the first del line to find the iface

    # Second pass: run all non-del commands
    for line in commands.splitlines():
        line = line.strip()
        if not line or line.startswith("#"): continue
        if "qdisc del" in line or "qdisc del" in line.replace("sudo ", ""):
            continue  # already handled by force_clear
        cmd = line.replace("sudo ", "")
        try:
            r = subprocess.run(["sudo"] + cmd.split(), capture_output=True, text=True)
            if r.returncode != 0 and "Cannot find device" not in r.stderr \
               and "No such" not in r.stderr:
                return False, r.stderr
        except Exception as e:
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


# ── Canvas-drawn round-thumb slider ──────────────────────────────────────────
class Slider(tk.Frame):
    """Slider with a round glowing thumb drawn on a Canvas."""

    def __init__(self, parent, label, from_, to, fmt, callback, **kw):
        super().__init__(parent, bg=BG, **kw)
        self.fmt      = fmt
        self.callback = callback
        self._min     = from_
        self._max     = to
        self._val     = from_
        self._dragging = False

        # Label row
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x")
        tk.Label(top, text=label, bg=BG, fg=FG2,
                 font=(FONT, 10)).pack(side="left")
        self.val_lbl = tk.Label(top, text=fmt(from_), bg=BG, fg=FG,
                                font=(FONT, 10, "bold"))
        self.val_lbl.pack(side="right")

        # Canvas for the custom slider
        self.cv = tk.Canvas(self, bg=BG, height=SLIDER_H,
                            highlightthickness=0, bd=0)
        self.cv.pack(fill="x", pady=(4, 0))

        self._track_bg  = None
        self._track_fg  = None
        self._thumb     = None
        self._thumb_glow = None

        self.cv.bind("<Configure>",     self._draw)
        self.cv.bind("<ButtonPress-1>", self._on_press)
        self.cv.bind("<B1-Motion>",     self._on_drag)
        self.cv.bind("<ButtonRelease-1>", self._on_release)

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
        w  = cv.winfo_width()
        h  = cv.winfo_height()
        if w < 4: return
        cy = h // 2
        x1, x2 = self._track_x_range()
        tx = self._val_to_x(self._val)

        cv.delete("all")

        # Background track
        cv.create_line(x1, cy, x2, cy, fill=BORDER, width=TRACK_H,
                       capstyle="round")
        # Filled (active) track
        cv.create_line(x1, cy, max(x1, tx), cy, fill=FG2, width=TRACK_H,
                       capstyle="round")

        # Glow ring behind thumb
        gr = THUMB_R + 4
        cv.create_oval(tx - gr, cy - gr, tx + gr, cy + gr,
                       fill="#2a2a2a", outline="", tags="glow")

        # Thumb circle
        cv.create_oval(tx - THUMB_R, cy - THUMB_R,
                       tx + THUMB_R, cy + THUMB_R,
                       fill=FG, outline=FG2, width=1, tags="thumb")

    def _on_press(self, e):
        self._dragging = True
        self._set_from_x(e.x)

    def _on_drag(self, e):
        if self._dragging:
            self._set_from_x(e.x)

    def _on_release(self, e):
        self._dragging = False
        self._set_from_x(e.x)

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
        self.bind("<Enter>",           lambda _: self.itemconfig(self._shape, fill=SURFACE2) if not self._active else None)
        self.bind("<Leave>",           lambda _: self.itemconfig(self._shape, fill=self._active_bg if self._active else self._bg))

    def _on_release(self, _):
        self.itemconfig(self._shape, fill=self._active_bg if self._active else self._bg)
        if self._command: self._command()

    def set_active(self, state):
        self._active = state
        self.itemconfig(self._shape,
                        fill=self._active_bg if state else self._bg,
                        outline=self._active_bdr if state else self._border)


# ── Rounded action button ─────────────────────────────────────────────────────
class RoundedButton(tk.Canvas):
    def __init__(self, parent, text, command=None,
                 accent=False, padx=20, pady=10, **kw):
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("bd", 0)
        super().__init__(parent, bg=BG, cursor="hand2", **kw)
        self._command  = command
        self._bg       = ACCENT if accent else SURFACE
        self._hover_bg = "#3a8ee6" if accent else SURFACE2
        self._fg       = "white" if accent else FG
        self._border   = ACCENT if accent else BORDER
        self._font     = (FONT, 10, "bold" if accent else "normal")

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
        self.bind("<Enter>",           lambda _: self.itemconfig(self._shape, fill=self._hover_bg))
        self.bind("<Leave>",           lambda _: self.itemconfig(self._shape, fill=self._bg))

    def set_text(self, t): self.itemconfig(self._label, text=t)


# ── Rounded text box ──────────────────────────────────────────────────────────
class RoundedText(tk.Frame):
    def __init__(self, parent, height=7, **kw):
        super().__init__(parent, bg=BG, **kw)
        self._rf = RoundedFrame(self, radius=12, fill=SURFACE, outline=BORDER)
        self._rf.pack(fill="both", expand=True)
        self.text = tk.Text(
            self._rf.inner, bg=SURFACE, fg=FG, font=(MONO, 10),
            relief="flat", bd=0, height=height, wrap="word",
            state="disabled", selectbackground=SURFACE2,
            insertbackground=FG, cursor="arrow"
        )
        self.text.tag_configure("comment", foreground=FG3)
        self.text.pack(fill="both", expand=True)

    def set_text(self, content):
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        for line in content.splitlines():
            tag = "comment" if line.startswith("#") else ""
            self.text.insert("end", line + "\n", tag)
        self.text.config(state="disabled")


# ── Main App ──────────────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Network Conditioner")
        self.configure(bg=BG)
        self.resizable(True, False)
        self.minsize(760, 680)

        style = ttk.Style(self)
        style.theme_use("clam")

        # Combobox — force all text states to FG on SURFACE2
        style.configure("Dark.TCombobox",
                        fieldbackground=SURFACE2,
                        background=SURFACE2,
                        foreground=FG,
                        selectforeground=FG,
                        selectbackground=SURFACE2,
                        bordercolor=BORDER,
                        arrowcolor=FG,
                        arrowsize=14,
                        relief="flat",
                        padding=(14, 14))
        style.map("Dark.TCombobox",
                  fieldbackground=[("readonly", SURFACE2),
                                   ("focus",    SURFACE2),
                                   ("active",   SURFACE2)],
                  foreground=[("readonly", FG),
                               ("focus",    FG),
                               ("active",   FG),
                               ("disabled", FG2)],
                  selectforeground=[("readonly", FG),
                                    ("focus",    FG)],
                  selectbackground=[("readonly", SURFACE2),
                                    ("focus",    SURFACE2)],
                  background=[("readonly", SURFACE2),
                               ("active",   SURFACE2)])

        self.interfaces   = get_interfaces()
        self._current_cmd = ""
        self._build_ui()
        self._update()

    def _build_ui(self):
        wrap = tk.Frame(self, bg=BG)
        wrap.pack(fill="both", expand=True, padx=20, pady=16)

        # ── Interface ────────────────────────────────────────────────────────
        irow = tk.Frame(wrap, bg=BG)
        irow.pack(fill="x", pady=(0, 16))
        tk.Label(irow, text="Interface", bg=BG, fg=FG2,
                 font=(FONT, 11)).pack(side="left", padx=(0, 14))

        self.iface_var = tk.StringVar(value=self.interfaces[0])
        # Use a plain frame with a border drawn via highlightbackground
        cb_outer = tk.Frame(irow, bg=BORDER, padx=1, pady=1)
        cb_outer.pack(side="left", fill="x", expand=True)
        cb_inner = tk.Frame(cb_outer, bg=SURFACE2)
        cb_inner.pack(fill="both", expand=True)

        cb = ttk.Combobox(cb_inner, textvariable=self.iface_var,
                          values=self.interfaces, state="readonly",
                          font=(FONT, 12), style="Dark.TCombobox")
        cb.pack(fill="both", expand=True)
        cb.bind("<<ComboboxSelected>>", lambda _: self._update())
        # Force redraw after selection to fix foreground flicker
        cb.bind("<<ComboboxSelected>>", lambda e: (self._update(), cb.selection_clear()), add=True)

        # ── Presets ──────────────────────────────────────────────────────────
        tk.Label(wrap, text="PRESETS", bg=BG, fg=FG2,
                 font=(FONT, 9)).pack(anchor="w", pady=(0, 8))
        prow = tk.Frame(wrap, bg=BG)
        prow.pack(fill="x", pady=(0, 18))
        self.preset_btns = []
        for name, vals in PRESETS:
            btn = PillButton(prow, text=name,
                             command=lambda v=vals, n=name: self._apply_preset(n, v),
                             font_size=10, padx=16, pady=8)
            btn.pack(side="left", padx=(0, 8))
            self.preset_btns.append((name, btn))

        # ── Sliders ───────────────────────────────────────────────────────────
        sg = tk.Frame(wrap, bg=BG)
        sg.pack(fill="x", pady=(0, 16))
        sg.columnconfigure(0, weight=1, uniform="col")
        sg.columnconfigure(1, weight=1, uniform="col")

        sep = tk.Frame(sg, bg=BORDER, width=1)
        sep.grid(row=0, column=2, rowspan=3, sticky="ns", padx=16)

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
            px = (0, 20) if col == 0 else (20, 0)
            s.grid(row=row, column=col, sticky="ew", padx=px, pady=(0, 14))
            self.sliders[key] = s

        # ── Stat cards ────────────────────────────────────────────────────────
        crow = tk.Frame(wrap, bg=BG)
        crow.pack(fill="x", pady=(0, 14))
        self.stat_vars = {}
        for label, key in [("DELAY", "delay"), ("PACKET LOSS", "loss"),
                            ("BANDWIDTH", "bw"), ("JITTER", "jitter")]:
            card = RoundedFrame(crow, radius=12, fill=BG, outline=BORDER,
                                outline_width=1, height=88)
            card.pack(side="left", fill="both", expand=True, padx=(0, 10))
            card.inner.config(bg=BG)
            center = tk.Frame(card.inner, bg=BG)
            center.place(relx=0.5, rely=0.5, anchor="center")
            tk.Label(center, text=label, bg=BG, fg=FG2,
                     font=(FONT, 9)).pack(anchor="center")
            sv = tk.StringVar(value="0 ms" if key in ("delay", "jitter")
                              else ("No limit" if key == "bw" else "0 %"))
            tk.Label(center, textvariable=sv, bg=BG, fg=FG,
                     font=(FONT, 22, "bold")).pack(anchor="center", pady=(2, 0))
            self.stat_vars[key] = sv

        # ── Command box ───────────────────────────────────────────────────────
        self.cmd_box = RoundedText(wrap, height=8)
        self.cmd_box.pack(fill="x", pady=(0, 14))

        # ── Buttons ───────────────────────────────────────────────────────────
        brow = tk.Frame(wrap, bg=BG)
        brow.pack(fill="x")

        self.copy_btn = RoundedButton(brow, text="⎘  Copy command",
                                      command=self._copy, padx=20, pady=10)
        self.copy_btn.pack(side="left", padx=(0, 10))

        self.apply_btn = RoundedButton(brow, text="▶  Apply to system",
                                       command=self._apply, accent=True,
                                       padx=20, pady=10)
        self.apply_btn.pack(side="left", padx=(0, 10))

        self.reset_btn = RoundedButton(brow, text="Reset",
                                       command=self._reset, padx=20, pady=10)
        self.reset_btn.pack(side="left")

        self.status_lbl = tk.Label(brow, text="", bg=BG, fg=FG2, font=(FONT, 9))
        self.status_lbl.pack(side="left", padx=14)

    # ── Logic ─────────────────────────────────────────────────────────────────
    def _update(self, _=None):
        iface   = self.iface_var.get()
        delay   = self.sliders["delay"].get()
        jitter  = self.sliders["jitter"].get()
        loss    = self.sliders["loss"].get()
        bw      = self.sliders["bw"].get()
        corrupt = self.sliders["corrupt"].get()
        dup     = self.sliders["dup"].get()

        # Jitter needs delay — show a warning hint on the jitter label
        jitter_label = f"{jitter} ms"
        if jitter > 0 and delay == 0:
            jitter_label = f"{jitter} ms ⚠ needs delay"
        self.sliders["jitter"].val_lbl.config(
            text=jitter_label,
            fg="#f0a500" if (jitter > 0 and delay == 0) else FG
        )

        self.stat_vars["delay"].set(f"{delay} ms")
        self.stat_vars["loss"].set(f"{loss} %")
        self.stat_vars["bw"].set(bw_label(bw))
        self.stat_vars["jitter"].set(f"{jitter} ms" if delay > 0 or jitter == 0
                                     else f"{jitter} ms*")

        self._current_cmd = build_command(iface, delay, jitter, loss, bw, corrupt, dup)
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
        self.apply_btn.set_text("Applying…")
        self.update()
        ok, err = run_tc(self._current_cmd)
        self.apply_btn.set_text("▶  Apply to system")
        if ok:
            self.status_lbl.config(text="✓ Applied", fg="#4caf50")
            self.after(4000, lambda: self.status_lbl.config(text=""))
        else:
            self.status_lbl.config(text="✗ Error", fg="#f44336")
            messagebox.showerror("Apply failed", f"tc returned:\n\n{err}")

    def _reset(self):
        for n, btn in self.preset_btns: btn.set_active(False)
        for s in self.sliders.values(): s.set(0)
        self._update()
        iface = self.iface_var.get()
        ok, _ = run_tc(f"sudo tc qdisc del dev {iface} root 2>/dev/null")
        if ok:
            self.status_lbl.config(text="✓ Reset", fg="#4caf50")
            self.after(4000, lambda: self.status_lbl.config(text=""))
        else:
            self.status_lbl.config(text="✗ Error", fg="#f44336")


if __name__ == "__main__":
    app = App()
    app.mainloop()