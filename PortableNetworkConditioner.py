#!/usr/bin/env python3
"""
Network Conditioner - Bidirectional, Cross-Platform, Responsive UI
Run with: sudo python3 network_conditioner.py

Linux  requires: iproute2   (sudo apt install iproute2)
                 python3-tk (sudo apt install python3-tk)
macOS  requires: pfctl / dnctl (built-in on macOS >= 10.7)
                 tkinter     (brew install python-tk  OR  comes with python.org installer)

UI notes
  * No page scrolling. The layout adapts to the window instead:
      - Wide windows  : two columns (controls | summary + commands)
      - Narrow windows: single column; the command preview can be toggled
  * All fonts and controls scale with the window size.
  * The action bar (Copy / Reset / Apply) is always pinned to the bottom.
"""

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox
import subprocess, sys, os, re, time, shutil

# -- Platform -----------------------------------------------------------------
IS_LINUX = sys.platform.startswith("linux")
IS_MAC   = sys.platform == "darwin"

# -- Dependency + privilege checks (run before any GUI) -----------------------

def _offer_install(pkg_name, install_cmd):
    """Show a plain terminal prompt asking whether to install a missing tool."""
    print(f"\n[Network Conditioner] Missing dependency: {pkg_name}")
    print(f"  Install command: {install_cmd}")
    ans = input("  Install now? [y/N] ").strip().lower()
    if ans == "y":
        try:
            subprocess.run(install_cmd.split(), check=True)
            print(f"  + {pkg_name} installed.")
        except subprocess.CalledProcessError as e:
            print(f"  x Install failed: {e}")
            sys.exit(1)
    else:
        print("  Aborting -- dependency not met.")
        sys.exit(1)


def check_dependencies():
    """
    Verify that the required back-end tools are present.
    If not, offer to install them and exit if the user declines.
    """
    if IS_LINUX:
        if not shutil.which("tc"):
            _offer_install(
                "iproute2 (tc)",
                "sudo apt-get install -y iproute2"
            )
        try:
            import tkinter  # noqa: F401
        except ImportError:
            _offer_install(
                "python3-tk",
                "sudo apt-get install -y python3-tk"
            )

    elif IS_MAC:
        missing = [t for t in ("pfctl", "dnctl") if not shutil.which(t)]
        if missing:
            print(
                f"\n[Network Conditioner] Required tools not found: {', '.join(missing)}\n"
                "These are built into macOS 10.7+.\n"
                "If you're on an older macOS, upgrade your OS.\n"
            )
            sys.exit(1)
        try:
            import tkinter  # noqa: F401
        except ImportError:
            print(
                "\n[Network Conditioner] tkinter not found.\n"
                "Install it with:  brew install python-tk\n"
                "Or download Python from https://www.python.org\n"
            )
            sys.exit(1)

    else:
        print(f"[Network Conditioner] Unsupported platform: {sys.platform}")
        sys.exit(1)


def check_sudo():
    """
    Abort with a clear message if the script is not running as root.
    tc / pfctl / dnctl all require elevated privileges.
    """
    if os.geteuid() != 0:
        msg = (
            "\n[Network Conditioner] This tool must be run as root.\n"
            f"  Re-run with:  sudo python3 {os.path.basename(sys.argv[0])}\n"
        )
        print(msg)
        try:
            import tkinter as _tk
            root = _tk.Tk(); root.withdraw()
            _tk.messagebox.showerror(
                "Permission denied",
                "This tool must be run as root (sudo).\n\n"
                f"Re-run with:\n  sudo python3 {os.path.basename(sys.argv[0])}"
            )
            root.destroy()
        except Exception:
            pass
        sys.exit(1)


# Run checks before building the UI
check_dependencies()
check_sudo()


# -- Palette ------------------------------------------------------------------
BG       = "#15171a"
SURFACE  = "#1e2126"
SURFACE2 = "#282c33"
SURFACE3 = "#323741"
BORDER   = "#353a43"
FG       = "#eceff4"
FG2      = "#9aa3b2"
FG3      = "#5d6675"
ACCENT   = "#4a9eff"
GREEN    = "#3ecf8e"
ORANGE   = "#f0a500"
RED      = "#f2555a"
EGRESS_COLOR  = "#4a9eff"
INGRESS_COLOR = "#a855f7"
BOTH_COLOR    = "#22c55e"

# Fonts are created after the Tk root exists (see setup_fonts)
FONT = "TkDefaultFont"
MONO = "Courier"
F = {}

BW_STEPS = [0, 64, 128, 250, 500, 1000, 2000, 3000, 5000,
            10000, 20000, 50000, 100000]      # Kbit/s, 0 = no limit

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

MODE_EGRESS  = "egress"
MODE_INGRESS = "ingress"
MODE_BOTH    = "both"


# -- Helpers ------------------------------------------------------------------
def get_interfaces():
    try:
        if IS_MAC:
            out = subprocess.check_output(["networksetup", "-listallhardwareports"],
                                          text=True)
            ifaces = re.findall(r"Device:\s+(\S+)", out)
            ifaces = [i for i in ifaces if not i.startswith("lo")]
            return ifaces if ifaces else ["en0"]
        else:
            out = subprocess.check_output(["ip", "-o", "link", "show"], text=True)
            ifaces = re.findall(r"\d+:\s+(\S+):", out)
            ifaces = [i for i in ifaces if i != "lo"
                      and not i.startswith("docker")
                      and not i.startswith("br-")
                      and not i.startswith("ifb")]
            return ifaces if ifaces else ["eth0"]
    except Exception:
        return ["en0"] if IS_MAC else ["eth0"]


def bw_label(v):
    if v == 0:    return "No limit"
    if v >= 1000: return f"{v // 1000} Mbit/s"
    return f"{v} Kbit/s"


def netem_args(delay, jitter, loss, corrupt, dup):
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


# -- Linux tc command builders (unchanged) ------------------------------------

def build_egress_commands(iface, delay, jitter, loss, bw, corrupt, dup):
    lines = ["# -- Egress (outgoing) -----------------------------------------------",
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
        lines.append("# No egress conditions -- rules cleared")
    return lines


def build_ingress_commands(iface, delay, jitter, loss, bw, corrupt, dup):
    netem = netem_args(delay, jitter, loss, corrupt, dup)
    if not netem and bw == 0:
        return [
            "# -- Ingress: No limit -- remove all ingress rules -------------------",
            f"sudo tc filter del dev {iface} parent ffff:",
            f"sudo tc filter del dev {iface} ingress",
            f"sudo tc qdisc  del dev {iface} ingress",
            f"sudo tc qdisc  del dev ifb0 root",
            f"sudo ip link   set ifb0 down",
            f"sudo ip link   del ifb0",
        ]
    lines = ["# -- Ingress (incoming) via IFB --------------------------------------"]
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
    if IS_MAC:
        return build_command_mac(iface, delay, jitter, loss, bw, corrupt, dup, mode)
    if mode == MODE_EGRESS:
        return "\n".join(build_egress_commands(iface, delay, jitter, loss, bw, corrupt, dup))
    elif mode == MODE_INGRESS:
        return "\n".join(build_ingress_commands(iface, delay, jitter, loss, bw, corrupt, dup))
    else:
        eg  = build_egress_commands(iface, delay, jitter, loss, bw, corrupt, dup)
        ing = build_ingress_commands(iface, delay, jitter, loss, bw, corrupt, dup)
        return "\n".join(eg + [""] + ing)


# -- macOS dnctl/pfctl command builders ---------------------------------------
#
# macOS uses a different stack:
#   - dnctl  : Dummynet -- latency, bandwidth, loss, queue
#   - pfctl  : pf firewall -- attach dummynet pipes to traffic
#
# Pipe numbers:
#   Pipe 1 -> egress (outgoing from iface)
#   Pipe 2 -> ingress (incoming to iface)
#
# Limitations vs Linux netem:
#   - No per-packet corruption or duplication (dnctl does not support these)
#   - Jitter is shown as a comment (dnctl has no built-in Gaussian jitter)
# -----------------------------------------------------------------------------

def _dnctl_pipe_args(pipe_no, delay, jitter, loss, bw):
    args = [f"pipe {pipe_no}", "config"]
    if bw > 0:
        rate = f"{bw//1000}Mbit/s" if bw >= 1000 else f"{bw}Kbit/s"
        args.append(f"bw {rate}")
    if delay > 0:
        args.append(f"delay {delay}ms")
    if loss > 0:
        args.append(f"plr {loss / 100:.4f}")
    return " ".join(args)


def build_command_mac(iface, delay, jitter, loss, bw, corrupt, dup, mode):
    no_conditions = (delay == 0 and jitter == 0 and loss == 0 and bw == 0)
    lines = [f"# -- macOS Network Conditioner -- interface: {iface} -----------------"]

    if no_conditions:
        lines += [
            "# No limit -- remove dummynet rules",
            "sudo pfctl -f /etc/pf.conf",
            "sudo dnctl pipe delete 1 2>/dev/null",
            "sudo dnctl pipe delete 2 2>/dev/null",
        ]
        return "\n".join(lines)

    if mode in (MODE_EGRESS, MODE_BOTH):
        lines += [
            "# -- Egress pipe (outgoing) ------------------------------------------",
            f"sudo dnctl {_dnctl_pipe_args(1, delay, jitter, loss, bw)}",
        ]
        if jitter > 0:
            lines.append(f"# Note: jitter +/-{jitter}ms -- dnctl uses fixed delay only")
    if mode in (MODE_INGRESS, MODE_BOTH):
        lines += [
            "# -- Ingress pipe (incoming) -----------------------------------------",
            f"sudo dnctl {_dnctl_pipe_args(2, delay, jitter, loss, bw)}",
        ]
        if jitter > 0:
            lines.append(f"# Note: jitter +/-{jitter}ms -- dnctl uses fixed delay only")

    lines += [
        "# -- pf rules (written to /tmp/pf_conditioner.conf) -----------------",
        "# Then loaded with: sudo pfctl -f /tmp/pf_conditioner.conf",
    ]
    if mode in (MODE_EGRESS, MODE_BOTH):
        lines.append(f"# dummynet out on {iface} all pipe 1")
    if mode in (MODE_INGRESS, MODE_BOTH):
        lines.append(f"# dummynet in  on {iface} all pipe 2")

    if corrupt > 0 or dup > 0:
        lines.append("# Note: corruption/duplication not supported by dnctl -- ignored on macOS")
    return "\n".join(lines)


# -- Linux tc execution helpers (unchanged) -----------------------------------

def _run(*args):
    r = subprocess.run(["sudo"] + list(args), capture_output=True, text=True)
    return r.returncode == 0, r.stderr


def _qdisc_show(iface):
    r = subprocess.run(["sudo", "tc", "qdisc", "show", "dev", iface],
                       capture_output=True, text=True)
    return r.stdout


def _has_ingress(iface):
    out = _qdisc_show(iface)
    return "ingress" in out or "ffff:" in out


def _nuke_ingress(iface):
    _run("tc", "filter", "del", "dev", iface, "parent", "ffff:")
    _run("tc", "filter", "del", "dev", iface, "ingress")
    time.sleep(0.05)
    _run("tc", "qdisc", "del", "dev", iface, "ingress")
    _run("tc", "qdisc", "del", "dev", iface, "handle", "ffff:", "ingress")
    _run("tc", "qdisc", "del", "dev", iface, "clsact")


def _wait_until_clear(iface, timeout=4.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _has_ingress(iface):
            return True
        time.sleep(0.1)
    return False


def force_clear(iface):
    if IS_MAC:
        force_clear_mac()
        return
    _run("tc", "filter", "del", "dev", "ifb0")
    _run("tc", "qdisc",  "del", "dev", "ifb0", "root")
    _run("ip", "link",   "set", "ifb0", "down")
    _run("ip", "link",   "del", "ifb0")
    time.sleep(0.1)
    for attempt in range(8):
        if not _has_ingress(iface): break
        _nuke_ingress(iface)
        time.sleep(0.15 * (attempt + 1))
    _run("tc", "qdisc", "del", "dev", iface, "root")
    time.sleep(0.1)
    if _has_ingress(iface):
        _nuke_ingress(iface)
        _wait_until_clear(iface, timeout=4.0)


def apply_ingress(iface, netem_lines, on_line=None):
    def do(*args):
        r = subprocess.run(["sudo"] + list(args), capture_output=True, text=True)
        benign = ("already exists", "File exists")
        if r.returncode != 0 and not any(b in r.stderr for b in benign):
            return False, r.stderr.strip()
        return True, ""

    for mod in ("ifb", "act_mirred", "cls_matchall"):
        arg = ["numifbs=1"] if mod == "ifb" else []
        subprocess.run(["sudo", "modprobe", mod] + arg, capture_output=True)
    time.sleep(0.15)

    _run("ip", "link", "set", "ifb0", "down")
    _run("ip", "link", "del", "ifb0")
    time.sleep(0.1)
    ok, err = do("ip", "link", "add", "ifb0", "type", "ifb")
    if not ok: return False, f"ip link add ifb0: {err}"

    ok, err = do("ip", "link", "set", "ifb0", "up")
    if not ok: return False, f"ip link set ifb0 up: {err}"

    for _ in range(10):
        r = subprocess.run(["ip", "link", "show", "ifb0"], capture_output=True, text=True)
        if "UP" in r.stdout or "UNKNOWN" in r.stdout: break
        time.sleep(0.1)

    if _has_ingress(iface):
        _nuke_ingress(iface)
        if not _wait_until_clear(iface, timeout=5.0):
            return False, (
                f"Could not remove existing ingress qdisc on {iface} after 5s.\n"
                f"Run manually:\n"
                f"  sudo tc qdisc del dev {iface} ingress\n"
                f"  sudo tc qdisc del dev {iface} handle ffff: ingress\n"
                f"Then try Apply again.")

    ok, err = do("tc", "qdisc", "add", "dev", iface, "ingress")
    if not ok: return False, f"tc qdisc add ingress: {err}"
    time.sleep(0.15)

    ok, err = do("tc", "filter", "add", "dev", iface,
                 "parent", "ffff:", "matchall",
                 "action", "mirred", "egress", "redirect", "dev", "ifb0")
    if not ok: return False, f"tc filter add matchall mirred: {err}"
    time.sleep(0.1)

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
    if IS_MAC:
        return run_tc_mac(commands, iface, mode, on_line)

    force_clear(iface)

    active = [l.strip() for l in commands.splitlines()
              if l.strip() and not l.strip().startswith("#")]
    clear_only_keys = ("filter del", "qdisc  del", "link   set", "link   del",
                       "qdisc del", "link set", "link del")
    if not active or all(any(k in l for k in clear_only_keys) for l in active):
        return True, "cleared"

    if mode in (MODE_INGRESS, MODE_BOTH):
        ok, err = apply_ingress(iface, commands.splitlines(), on_line=on_line)
        if not ok: return False, err
        if mode == MODE_INGRESS:
            return True, ""

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


# -- macOS dnctl/pfctl execution ----------------------------------------------

def force_clear_mac():
    subprocess.run(["sudo", "dnctl", "pipe", "delete", "1"], capture_output=True)
    subprocess.run(["sudo", "dnctl", "pipe", "delete", "2"], capture_output=True)
    # Restore the system's default pf ruleset -- this is what actually
    # removes the dummynet passthrough rules written by the apply step.
    subprocess.run(["sudo", "pfctl", "-f", "/etc/pf.conf"], capture_output=True)
    subprocess.run(["sudo", "pfctl", "-a", "conditioner", "-F", "all"],
                   capture_output=True)


def run_tc_mac(commands, iface, mode=None, on_line=None):
    active = [l.strip() for l in commands.splitlines()
              if l.strip() and not l.strip().startswith("#")]

    # The "No limit" branch of build_command_mac renders lines meant for a
    # real shell -- e.g. "sudo dnctl pipe delete 1 2>/dev/null" -- where
    # "2>/dev/null" quietly no-ops a delete of a pipe that doesn't exist.
    # subprocess.run() has no shell to interpret that redirection, so
    # executing this text literally passes "2>/dev/null" through as a
    # bogus dnctl argument and breaks it ("Invalid argument"). Detect this
    # specific "clear everything" case by its marker comment and route it
    # through the dedicated, redirect-free helper instead of running the
    # display text.
    if not active or "# No limit -- remove dummynet rules" in commands:
        if on_line:
            for line in commands.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    on_line(line, "running")
        force_clear_mac()
        if on_line:
            for line in commands.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    on_line(line, "ok")
        return True, "cleared"

    BENIGN = ("not found", "No such", "already exists")
    for line in commands.splitlines():
        line = line.strip()
        if not line or line.startswith("#"): continue
        if "pfctl -f /tmp" in line or "pf_conditioner" in line: continue
        if on_line: on_line(line, "running")
        # Defensive: strip any shell-only redirection before tokenizing --
        # subprocess.run() has no shell, so "2>/dev/null" etc. would
        # otherwise be passed through as a literal (and invalid) argument.
        clean_line = re.sub(r"\s+\d*>\s*\S+", "", line)
        cmd = clean_line.replace("sudo ", "").split()
        try:
            r = subprocess.run(["sudo"] + cmd, capture_output=True, text=True)
            if r.returncode != 0 and not any(b in r.stderr for b in BENIGN):
                if on_line: on_line(line, "error")
                return False, r.stderr
            if on_line: on_line(line, "ok")
        except Exception as e:
            if on_line: on_line(line, "error")
            return False, str(e)

    pf_conf_lines = ["# Generated by Network Conditioner\n"]
    if mode in (MODE_EGRESS, MODE_BOTH):
        pf_conf_lines.append(f"dummynet out on {iface} all pipe 1\n")
    if mode in (MODE_INGRESS, MODE_BOTH):
        pf_conf_lines.append(f"dummynet in  on {iface} all pipe 2\n")

    conf_path = "/tmp/pf_conditioner.conf"
    try:
        with open(conf_path, "w") as f:
            f.writelines(pf_conf_lines)
    except OSError as e:
        return False, f"Could not write pf config: {e}"

    if on_line: on_line(f"sudo pfctl -f {conf_path}", "running")
    r = subprocess.run(["sudo", "pfctl", "-e", "-f", conf_path],
                       capture_output=True, text=True)
    if r.returncode != 0 and "pf already enabled" not in r.stderr:
        if on_line: on_line(f"sudo pfctl -f {conf_path}", "error")
        return False, r.stderr
    if on_line: on_line(f"sudo pfctl -f {conf_path}", "ok")
    return True, ""


# =============================================================================
#  UI
# =============================================================================

# -- Fonts (named fonts: resizing them updates every widget that uses them) ----
def setup_fonts(root):
    global FONT, MONO
    fams = set(tkfont.families(root))
    FONT = next((f for f in ("Inter", "SF Pro Text", "Helvetica Neue", "Segoe UI",
                             "Ubuntu", "Noto Sans", "Cantarell", "DejaVu Sans",
                             "Helvetica") if f in fams), "TkDefaultFont")
    MONO = next((f for f in ("JetBrains Mono", "SF Mono", "Menlo", "Consolas",
                             "DejaVu Sans Mono", "Ubuntu Mono", "Liberation Mono",
                             "Courier New") if f in fams), "Courier")
    mk = lambda **kw: tkfont.Font(root=root, **kw)
    F["base"]   = mk(family=FONT, size=10)
    F["small"]  = mk(family=FONT, size=9)
    F["cap"]    = mk(family=FONT, size=9,  weight="bold")
    F["bold"]   = mk(family=FONT, size=10, weight="bold")
    F["btn"]    = mk(family=FONT, size=10, weight="bold")
    F["title"]  = mk(family=FONT, size=17, weight="bold")
    F["big"]    = mk(family=FONT, size=19, weight="bold")
    F["mono"]   = mk(family=MONO, size=10)
    F["mono_s"] = mk(family=MONO, size=9)
    F["mono_b"] = mk(family=MONO, size=10, weight="bold")


_BASE_SIZES = {"base": 10, "small": 9, "cap": 9, "bold": 10, "btn": 10,
               "title": 17, "big": 19, "mono": 10, "mono_s": 9, "mono_b": 10}


def scale_fonts(s):
    for k, v in _BASE_SIZES.items():
        F[k].configure(size=max(8, round(v * s)))


# -- Colour / drawing helpers --------------------------------------------------
def _rgb(h):
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def tint(fg, bg, alpha):
    """Blend fg over bg with the given alpha -> hex colour."""
    r, g, b = _rgb(fg); br, bgc, bb = _rgb(bg)
    return "#{:02x}{:02x}{:02x}".format(int(br + (r - br) * alpha),
                                        int(bgc + (g - bgc) * alpha),
                                        int(bb + (b - bb) * alpha))


def darken(hex_color, amt=28):
    r, g, b = _rgb(hex_color)
    return f"#{max(0, r-amt):02x}{max(0, g-amt):02x}{max(0, b-amt):02x}"


def rounded_rect_points(x1, y1, x2, y2, r):
    r = max(1, min(r, (x2 - x1) // 2, (y2 - y1) // 2))
    return [x1+r, y1, x2-r, y1, x2, y1, x2, y1+r,
            x2, y2-r, x2, y2, x2-r, y2, x1+r, y2,
            x1, y2, x1, y2-r, x1, y1+r, x1, y1]


def panel(parent, bg=SURFACE):
    """Flat bordered container used for grouped controls."""
    return tk.Frame(parent, bg=bg, bd=0, highlightthickness=1,
                    highlightbackground=BORDER, highlightcolor=BORDER)


def caption(parent, text, bg=SURFACE):
    return tk.Label(parent, text=text, bg=bg, fg=FG2, font=F["cap"], anchor="w")


# -- Button (chip / secondary / primary) --------------------------------------
class Btn(tk.Canvas):
    """Stretchable rounded button. kind: 'chip' | 'secondary' | 'primary'."""

    def __init__(self, parent, text, command=None, kind="secondary",
                 parent_bg=BG, accent=ACCENT, pady=8):
        super().__init__(parent, bg=parent_bg, highlightthickness=0, bd=0,
                         width=10)
        self.text, self.command, self.kind = text, command, kind
        self._accent, self._pady = accent, pady
        self._active = self._hover = self._pressed = False
        self._enabled = True
        self.bind("<Configure>",       lambda e: self._draw())
        self.bind("<Enter>",           lambda e: self._set_hover(True))
        self.bind("<Leave>",           lambda e: self._set_hover(False))
        self.bind("<ButtonPress-1>",   self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.refresh()

    def _font(self):
        return F["btn"] if self.kind == "primary" else F["base"]

    def refresh(self):
        self.configure(height=self._font().metrics("linespace") + 2 * self._pady)
        self._draw()

    def _set_hover(self, v):
        self._hover = v
        self._pressed = self._pressed and v
        self._draw()

    def _on_press(self, _):
        if self._enabled:
            self._pressed = True; self._draw()

    def _on_release(self, e):
        was = self._pressed
        self._pressed = False
        self._draw()
        inside = 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height()
        if was and inside and self._enabled and self.command:
            self.command()

    def set_text(self, t):
        self.text = t; self._draw()

    def set_active(self, state):
        self._active = state; self._draw()

    def set_accent(self, color):
        self._accent = color; self._draw()

    def set_enabled(self, flag):
        self._enabled = flag
        self._draw()

    def _draw(self):
        w, h = self.winfo_width(), self.winfo_height()
        self.delete("all")
        if w < 8 or h < 8:
            return
        r = h // 2 if self.kind == "chip" else 8

        if not self._enabled:
            fill, outline, fg = SURFACE2, BORDER, FG3
        elif self.kind == "primary":
            fill = darken(self._accent, 40 if self._pressed else 18 if self._hover else 0)
            outline, fg = fill, "white"
        else:
            base = SURFACE3 if self._pressed else (SURFACE3 if self._hover else SURFACE2)
            fill, outline, fg = base, BORDER, FG
            if self.kind == "chip" and self._active:
                fill, outline = tint(self._accent, SURFACE, 0.22), self._accent

        self.create_polygon(rounded_rect_points(1, 1, w-1, h-1, r), smooth=True,
                            fill=fill, outline=outline, width=1)
        self.create_text(w // 2, h // 2, text=self.text, fill=fg, font=self._font())


# -- Segmented control (direction) --------------------------------------------
class Segmented(tk.Canvas):
    def __init__(self, parent, modes, callback, parent_bg=SURFACE):
        super().__init__(parent, bg=parent_bg, highlightthickness=0, bd=0,
                         width=10)
        self.modes, self.callback = modes, callback
        self._mode = modes[0][0]
        self._hover = None
        self.bind("<Configure>",       lambda e: self._draw())
        self.bind("<Motion>",          self._on_motion)
        self.bind("<Leave>",           lambda e: self._set_hover(None))
        self.bind("<ButtonRelease-1>", self._on_click)
        self.refresh()

    def refresh(self):
        self.configure(height=F["base"].metrics("linespace") + 16)
        self._draw()

    def _seg_at(self, x):
        w = self.winfo_width()
        n = len(self.modes)
        if w < 10: return None
        i = int((x - 4) / max(1, (w - 8) / n))
        return self.modes[i][0] if 0 <= i < n else None

    def _set_hover(self, m):
        if m != self._hover:
            self._hover = m; self._draw()

    def _on_motion(self, e): self._set_hover(self._seg_at(e.x))

    def _on_click(self, e):
        m = self._seg_at(e.x)
        if m and m != self._mode:
            self._mode = m
            self._draw()
            self.callback(m)

    def get(self): return self._mode

    def set(self, m):
        self._mode = m; self._draw()

    def _draw(self):
        w, h = self.winfo_width(), self.winfo_height()
        self.delete("all")
        if w < 20 or h < 10: return
        self.create_polygon(rounded_rect_points(1, 1, w-1, h-1, h // 2), smooth=True,
                            fill=BG, outline=BORDER, width=1)
        n = len(self.modes)
        sw = (w - 8) / n
        for i, (mode, label, color) in enumerate(self.modes):
            x1 = 4 + i * sw; x2 = x1 + sw
            active = mode == self._mode
            if active:
                self.create_polygon(rounded_rect_points(int(x1), 4, int(x2), h-4, (h-8)//2),
                                    smooth=True, fill=color, outline=color)
            fg = "white" if active else (FG if mode == self._hover else FG2)
            self.create_text((x1 + x2) / 2, h // 2, text=label, fill=fg,
                             font=F["btn"] if active else F["base"])


# -- Slider -------------------------------------------------------------------
class Slider(tk.Frame):
    def __init__(self, parent, label, lo, hi, fmt, callback, steps=None,
                 parent_bg=SURFACE):
        super().__init__(parent, bg=parent_bg)
        self._bg = parent_bg
        self.fmt, self.callback, self.steps = fmt, callback, steps
        self._lo, self._hi = (0, len(steps) - 1) if steps else (lo, hi)
        self._pos = self._lo
        self._enabled = True
        self._accent = EGRESS_COLOR
        self._suffix, self._val_color = "", FG
        self._dragging = False

        top = tk.Frame(self, bg=parent_bg); top.pack(fill="x")
        self.name_lbl = tk.Label(top, text=label, bg=parent_bg, fg=FG2, font=F["base"])
        self.name_lbl.pack(side="left")
        self.val_lbl = tk.Label(top, bg=parent_bg, fg=FG, font=F["bold"])
        self.val_lbl.pack(side="right")

        self.cv = tk.Canvas(self, bg=parent_bg, highlightthickness=0, bd=0)
        self.cv.pack(fill="x", pady=(3, 0))
        self.cv.bind("<Configure>",       lambda e: self._draw())
        self.cv.bind("<ButtonPress-1>",   self._on_press)
        self.cv.bind("<B1-Motion>",       self._on_drag)
        self.cv.bind("<ButtonRelease-1>", self._on_release)
        self.refresh()

    # geometry ---------------------------------------------------------------
    def _thumb_r(self):
        return max(7, F["base"].metrics("linespace") // 2 - 1)

    def refresh(self):
        self.cv.configure(height=2 * (self._thumb_r() + 3))
        self._refresh_label()
        self._draw()

    def _range(self):
        r = self._thumb_r()
        return r + 3, max(r + 4, self.cv.winfo_width() - r - 3)

    def _pos_to_x(self, p):
        x1, x2 = self._range()
        return x1 + (p - self._lo) / max(self._hi - self._lo, 1) * (x2 - x1)

    def _x_to_pos(self, x):
        x1, x2 = self._range()
        ratio = max(0.0, min(1.0, (x - x1) / max(x2 - x1, 1)))
        return int(round(self._lo + ratio * (self._hi - self._lo)))

    # drawing ----------------------------------------------------------------
    def _draw(self):
        cv = self.cv
        w, h = cv.winfo_width(), cv.winfo_height()
        cv.delete("all")
        if w < 20: return
        cy = h // 2
        x1, x2 = self._range()
        tx = self._pos_to_x(self._pos)
        accent = self._accent if self._enabled else FG3
        th = max(4, h // 6)
        cv.create_line(x1, cy, x2, cy, fill=SURFACE3, width=th, capstyle="round")
        if tx > x1:
            cv.create_line(x1, cy, tx, cy, fill=accent, width=th, capstyle="round")
        r = self._thumb_r()
        cv.create_oval(tx-r-2, cy-r-2, tx+r+2, cy+r+2,
                       fill=tint(accent, self._bg, 0.25), outline="")
        cv.create_oval(tx-r, cy-r, tx+r, cy+r, fill=FG if self._enabled else FG3,
                       outline=accent, width=2)

    def _refresh_label(self):
        if not self._enabled:
            self.val_lbl.config(text="Not supported", fg=FG3)
            self.name_lbl.config(fg=FG3)
        else:
            self.val_lbl.config(text=self.fmt(self.get()) + self._suffix,
                                fg=self._val_color)
            self.name_lbl.config(fg=FG2)

    # interaction ------------------------------------------------------------
    def _on_press(self, e):
        if self._enabled:
            self._dragging = True; self._set_from_x(e.x)

    def _on_drag(self, e):
        if self._dragging: self._set_from_x(e.x)

    def _on_release(self, e):
        if self._dragging:
            self._dragging = False; self._set_from_x(e.x)

    def _set_from_x(self, x):
        self._pos = self._x_to_pos(x)
        self._refresh_label()
        self._draw()
        self.callback()

    # public -----------------------------------------------------------------
    def get(self):
        return self.steps[self._pos] if self.steps else self._pos

    def set(self, v):
        if self.steps:
            self._pos = min(range(len(self.steps)), key=lambda i: abs(self.steps[i] - v))
        else:
            self._pos = max(self._lo, min(self._hi, int(v)))
        self._refresh_label(); self._draw()

    def set_accent(self, color):
        self._accent = color; self._draw()

    def set_note(self, suffix="", color=FG):
        self._suffix, self._val_color = suffix, color
        self._refresh_label()

    def set_enabled(self, flag):
        self._enabled = flag
        self._refresh_label(); self._draw()


# -- Status chip (header) -----------------------------------------------------
class StatusChip(tk.Canvas):
    def __init__(self, parent):
        super().__init__(parent, bg=BG, highlightthickness=0, bd=0)
        self._text, self._color = "Inactive", FG2
        self.refresh()

    def set(self, text, color):
        self._text, self._color = text, color
        self.refresh()

    def refresh(self):
        f = F["small"]
        h = f.metrics("linespace") + 12
        w = f.measure(self._text) + 40
        self.configure(width=w, height=h)
        self.delete("all")
        c = self._color
        self.create_polygon(rounded_rect_points(1, 1, w-1, h-1, h // 2), smooth=True,
                            fill=tint(c, BG, 0.14), outline=c)
        self.create_oval(13, h/2 - 3, 19, h/2 + 3, fill=c, outline="")
        self.create_text(28, h // 2, text=self._text, anchor="w", fill=c, font=f)


# -- Command preview ----------------------------------------------------------
class CommandView(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg=SURFACE, bd=0, highlightthickness=1,
                         highlightbackground=BORDER, highlightcolor=BORDER)
        self.text = tk.Text(self, bg=SURFACE, fg=FG, font=F["mono"],
                            relief="flat", bd=0, highlightthickness=0,
                            height=3, width=10, wrap="word", state="disabled",
                            padx=12, pady=10, spacing1=2,
                            selectbackground=SURFACE3)
        self.text.pack(fill="both", expand=True)
        self.text.tag_configure("comment",  foreground=FG3, font=F["mono_s"])
        self.text.tag_configure("section",  foreground=FG2, font=F["mono_s"])
        self.text.tag_configure("running",  foreground="#000000", background=ORANGE,
                                font=F["mono_b"])
        self.text.tag_configure("done_ok",  foreground=GREEN)
        self.text.tag_configure("done_err", foreground=RED)
        self._lines = []

    def set_text(self, content):
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self._lines = content.splitlines()
        for line in self._lines:
            if line.startswith("# --"):
                self.text.insert("end", line + "\n", "section")
            elif line.startswith("#"):
                self.text.insert("end", line + "\n", "comment")
            else:
                self.text.insert("end", line + "\n")
        self.text.config(state="disabled")

    @staticmethod
    def _norm(s):
        return s.strip().replace("sudo ", "")

    def highlight_line(self, line_text):
        self.text.config(state="normal")
        self.text.tag_remove("running", "1.0", "end")
        for i, line in enumerate(self._lines):
            if not line.startswith("#") and self._norm(line) == self._norm(line_text):
                self.text.tag_add("running", f"{i+1}.0", f"{i+2}.0")
                self.text.see(f"{i+1}.0")
                break
        self.text.config(state="disabled")

    def mark_done(self, line_text, success=True):
        self.text.config(state="normal")
        tag = "done_ok" if success else "done_err"
        for i, line in enumerate(self._lines):
            if not line.startswith("#") and self._norm(line) == self._norm(line_text):
                self.text.tag_remove("running", f"{i+1}.0", f"{i+2}.0")
                self.text.tag_add(tag,          f"{i+1}.0", f"{i+2}.0")
                break
        self.text.config(state="disabled")

    def clear_highlights(self):
        self.text.config(state="normal")
        for tag in ("running", "done_ok", "done_err"):
            self.text.tag_remove(tag, "1.0", "end")
        self.text.config(state="disabled")

    def set_border_color(self, color):
        self.configure(highlightbackground=color, highlightcolor=color)


# -- Main App -----------------------------------------------------------------
class App(tk.Tk):
    WIDE_MIN   = 880     # px: switch to two-column layout at/above this width
    TALL_MIN   = 690     # px: in single-column mode, auto-show commands above this height
    PAD        = 16

    MODES = [
        (MODE_EGRESS,  "\u2191  Egress",  EGRESS_COLOR),
        (MODE_BOTH,    "\u21C5  Both",    BOTH_COLOR),
        (MODE_INGRESS, "\u2193  Ingress", INGRESS_COLOR),
    ]
    MODE_NAMES = {MODE_EGRESS: "egress", MODE_INGRESS: "ingress", MODE_BOTH: "both directions"}

    def __init__(self):
        super().__init__()
        setup_fonts(self)
        platform_label = "macOS" if IS_MAC else "Linux"
        self.title(f"Network Conditioner ({platform_label})")
        self.configure(bg=BG)
        self.resizable(True, True)

        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w = min(1040, int(sw * 0.92)); h = min(740, int(sh * 0.88))
        self.geometry(f"{w}x{h}+{(sw - w)//2}+{max(0, (sh - h)//3)}")
        self.minsize(min(560, sw), min(500, sh))

        self._style_ttk()

        self.interfaces    = get_interfaces()
        self._current_cmd  = ""
        self._mode         = MODE_EGRESS
        self._busy         = False
        self._active       = False
        self._scale        = None
        self._layout_key   = None
        self._cmd_visible  = True
        self._cmd_user_set = False
        self._cfg_job      = None
        self.scalables     = []

        self._build_ui()
        self.bind("<Configure>", self._on_configure)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._on_mode_change(MODE_EGRESS)
        self._apply_responsive(w, h)

    # ---- styling ------------------------------------------------------------
    def _style_ttk(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Dark.TCombobox",
                        fieldbackground=SURFACE2, background=SURFACE2,
                        foreground=FG, selectforeground=FG,
                        selectbackground=SURFACE2, bordercolor=BORDER,
                        lightcolor=SURFACE2, darkcolor=SURFACE2,
                        arrowcolor=FG, arrowsize=14, relief="flat",
                        padding=(10, 7))
        style.map("Dark.TCombobox",
                  fieldbackground=[("readonly", SURFACE2), ("focus", SURFACE2), ("active", SURFACE2)],
                  foreground=[("readonly", FG), ("focus", FG), ("active", FG)],
                  selectforeground=[("readonly", FG), ("focus", FG)],
                  selectbackground=[("readonly", SURFACE2), ("focus", SURFACE2)],
                  background=[("readonly", SURFACE2), ("active", SURFACE3)])
        # dropdown list colours
        self.option_add("*TCombobox*Listbox.background", SURFACE2)
        self.option_add("*TCombobox*Listbox.foreground", FG)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", "white")
        self.option_add("*TCombobox*Listbox.font", F["base"])

    def _accent_color(self):
        return {MODE_EGRESS: EGRESS_COLOR,
                MODE_INGRESS: INGRESS_COLOR,
                MODE_BOTH: BOTH_COLOR}[self._mode]

    def _reg(self, widget):
        self.scalables.append(widget)
        return widget

    # ---- UI construction ----------------------------------------------------
    def _build_ui(self):
        PAD = self.PAD

        # Header ---------------------------------------------------------------
        hdr = tk.Frame(self, bg=BG)
        hdr.pack(side="top", fill="x", padx=PAD, pady=(14, 10))
        titles = tk.Frame(hdr, bg=BG); titles.pack(side="left")
        tk.Label(titles, text="Network Conditioner", bg=BG, fg=FG,
                 font=F["title"]).pack(anchor="w")
        sub = ("Simulate slow or unreliable networks on your machine"
               if not IS_MAC else
               "Simulate slow networks (macOS: latency, loss, bandwidth)")
        tk.Label(titles, text=sub, bg=BG, fg=FG2, font=F["small"]).pack(anchor="w")
        self.status_chip = StatusChip(hdr)
        self.status_chip.pack(side="right")

        # Toolbar: interface + direction ---------------------------------------
        tb = panel(self)
        tb.pack(side="top", fill="x", padx=PAD, pady=(0, 10))
        tbi = tk.Frame(tb, bg=SURFACE)
        tbi.pack(fill="x", padx=14, pady=12)
        tbi.columnconfigure(0, weight=2, uniform="tb")
        tbi.columnconfigure(1, weight=3, uniform="tb")

        caption(tbi, "Interface").grid(row=0, column=0, sticky="w")
        caption(tbi, "Apply to").grid(row=0, column=1, sticky="w", padx=(16, 0))

        self.iface_var = tk.StringVar(value=self.interfaces[0])
        self.iface_cb = ttk.Combobox(tbi, textvariable=self.iface_var,
                                     values=self.interfaces, state="readonly",
                                     font=F["base"], style="Dark.TCombobox")
        self.iface_cb.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.iface_cb.bind("<<ComboboxSelected>>",
                           lambda e: (self.iface_cb.selection_clear(), self._update()))

        self.direction = self._reg(Segmented(tbi, self.MODES, self._on_mode_change,
                                             parent_bg=SURFACE))
        self.direction.grid(row=1, column=1, sticky="ew", padx=(16, 0), pady=(4, 0))

        self.dir_desc = tk.Label(tbi, text="", bg=SURFACE, fg=EGRESS_COLOR,
                                 font=F["small"], anchor="w")
        self.dir_desc.grid(row=2, column=1, sticky="w", padx=(16, 0), pady=(5, 0))

        # Footer (packed before body so it is never clipped) --------------------
        foot = tk.Frame(self, bg=BG)
        foot.pack(side="bottom", fill="x")
        tk.Frame(foot, bg=BORDER, height=1).pack(fill="x")
        fi = tk.Frame(foot, bg=BG)
        fi.pack(fill="x", padx=PAD, pady=12)
        for c, wgt in enumerate((1, 1, 1, 2)):
            fi.columnconfigure(c, weight=wgt, uniform="foot")

        self.cmd_toggle = self._reg(Btn(fi, "Hide commands", self._toggle_commands))
        self.cmd_toggle.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self._foot_toggle_grid = dict(row=0, column=0, sticky="ew", padx=(0, 8))
        self.copy_btn = self._reg(Btn(fi, "Copy command", self._copy))
        self.copy_btn.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.reset_btn = self._reg(Btn(fi, "Reset", self._reset))
        self.reset_btn.grid(row=0, column=2, sticky="ew", padx=(0, 8))
        self.apply_btn = self._reg(Btn(fi, "Apply to system", self._apply,
                                       kind="primary", accent=self._accent_color()))
        self.apply_btn.grid(row=0, column=3, sticky="ew")

        # Body -----------------------------------------------------------------
        self.body = tk.Frame(self, bg=BG)
        self.body.pack(side="top", fill="both", expand=True, padx=PAD)

        self._build_controls_panel()
        self._build_right_column()

    def _build_controls_panel(self):
        self.left = panel(self.body)
        lp = tk.Frame(self.left, bg=SURFACE)
        lp.pack(fill="both", expand=True, padx=14, pady=12)

        caption(lp, "Presets").pack(anchor="w")
        pgrid = tk.Frame(lp, bg=SURFACE)
        pgrid.pack(fill="x", pady=(6, 8))
        for c in range(4):
            pgrid.columnconfigure(c, weight=1, uniform="preset")
        self.preset_btns = []
        for i, (name, vals) in enumerate(PRESETS):
            btn = self._reg(Btn(pgrid, name,
                                command=lambda v=vals, n=name: self._apply_preset(n, v),
                                kind="chip", parent_bg=SURFACE,
                                accent=self._accent_color(), pady=6))
            btn.grid(row=i // 4, column=i % 4, padx=3, pady=3, sticky="ew")
            self.preset_btns.append((name, btn))

        tk.Frame(lp, bg=BORDER, height=1).pack(fill="x", pady=(4, 10))

        caption(lp, "Conditions").pack(anchor="w", pady=(0, 6))
        sg = tk.Frame(lp, bg=SURFACE)
        sg.pack(fill="both", expand=True)
        sg.columnconfigure(0, weight=1, uniform="col")
        sg.columnconfigure(1, weight=1, uniform="col")
        for r in range(3):
            sg.rowconfigure(r, weight=1)

        ms  = lambda v: f"{v} ms"
        pct = lambda v: f"{v}%"
        sliders_def = [
            ("delay",   "Latency",         0, 2000, ms,       None,     0, 0),
            ("jitter",  "Jitter",          0, 500,  ms,       None,     0, 1),
            ("loss",    "Packet loss",     0, 100,  pct,      None,     1, 0),
            ("bw",      "Bandwidth limit", 0, 0,    bw_label, BW_STEPS, 1, 1),
            ("corrupt", "Corruption",      0, 100,  pct,      None,     2, 0),
            ("dup",     "Duplication",     0, 100,  pct,      None,     2, 1),
        ]
        self.sliders = {}
        for key, label, lo, hi, fmt, steps, row, col in sliders_def:
            s = self._reg(Slider(sg, label, lo, hi, fmt, self._on_slider,
                                 steps=steps, parent_bg=SURFACE))
            s.set_accent(self._accent_color())
            px = (0, 12) if col == 0 else (12, 0)
            s.grid(row=row, column=col, sticky="ew", padx=px, pady=6)
            self.sliders[key] = s

        if IS_MAC:   # dnctl cannot corrupt or duplicate packets
            self.sliders["corrupt"].set_enabled(False)
            self.sliders["dup"].set_enabled(False)

    def _build_right_column(self):
        self.right = tk.Frame(self.body, bg=BG)
        self.right.columnconfigure(0, weight=1)
        self.right.rowconfigure(1, weight=1)

        # Summary cards (wide layout only)
        self.stats = tk.Frame(self.right, bg=BG)
        self.stats.columnconfigure(0, weight=1, uniform="card")
        self.stats.columnconfigure(1, weight=1, uniform="card")
        self.stat_vars = {}
        defs = [("Latency", "delay"), ("Packet loss", "loss"),
                ("Bandwidth", "bw"), ("Jitter", "jitter")]
        for idx, (label, key) in enumerate(defs):
            card = panel(self.stats)
            card.grid(row=idx // 2, column=idx % 2, sticky="nsew",
                      padx=(0, 5) if idx % 2 == 0 else (5, 0),
                      pady=(0, 8) if idx < 2 else (0, 0))
            inner = tk.Frame(card, bg=SURFACE)
            inner.pack(fill="both", expand=True, padx=12, pady=8)
            tk.Label(inner, text=label, bg=SURFACE, fg=FG2, font=F["small"],
                     anchor="w").pack(anchor="w")
            sv = tk.StringVar(value="0 ms")
            tk.Label(inner, textvariable=sv, bg=SURFACE, fg=FG, font=F["big"],
                     anchor="w").pack(anchor="w")
            self.stat_vars[key] = sv

        # Command preview
        self.cmd_panel = tk.Frame(self.right, bg=BG)
        self.cmd_panel.columnconfigure(0, weight=1)
        self.cmd_panel.rowconfigure(1, weight=1)
        self.cmd_caption = caption(self.cmd_panel, "Commands to run", bg=BG)
        self.cmd_caption.grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.cmd_box = CommandView(self.cmd_panel)
        self.cmd_box.grid(row=1, column=0, sticky="nsew")

    # ---- Responsive layout --------------------------------------------------
    def _on_configure(self, e):
        if e.widget is not self:
            return
        if self._cfg_job:
            self.after_cancel(self._cfg_job)
        self._cfg_job = self.after(25, self._apply_responsive)

    def _apply_responsive(self, w=None, h=None):
        self._cfg_job = None
        w = w or self.winfo_width()
        h = h or self.winfo_height()
        if w < 50 or h < 50:
            return

        # 1) scale fonts + controls with window size
        s = max(0.85, min(1.3, min(w / 1000.0, h / 720.0)))
        s = round(s * 20) / 20
        if s != self._scale:
            self._scale = s
            scale_fonts(s)
            for wd in self.scalables:
                wd.refresh()
            self.status_chip.refresh()

        # 2) choose layout
        wide = w >= self.WIDE_MIN
        show_cmd = self._cmd_visible
        if not wide and not self._cmd_user_set:
            show_cmd = h >= self.TALL_MIN
        key = (wide, show_cmd)
        if key != self._layout_key:
            self._layout_key = key
            self._cmd_visible = show_cmd
            self._layout(wide, show_cmd)

    def _layout(self, wide, show_cmd):
        b = self.body
        for wd in (self.left, self.right):
            wd.grid_forget()
        for i in range(3):
            b.columnconfigure(i, weight=0)
            b.rowconfigure(i, weight=0)

        if wide:
            b.columnconfigure(0, weight=11, uniform="wide")
            b.columnconfigure(1, weight=9,  uniform="wide")
            b.rowconfigure(0, weight=1)
            self.left.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=(0, 12))
            self.right.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=(0, 12))
            self.stats.grid(row=0, column=0, sticky="ew", pady=(0, 12))
            self.cmd_panel.grid(row=1, column=0, sticky="nsew")
            self.cmd_toggle.grid_remove()
        else:
            b.columnconfigure(0, weight=1, uniform="")
            b.rowconfigure(0, weight=0 if show_cmd else 1)
            b.rowconfigure(1, weight=1 if show_cmd else 0)
            self.left.grid(row=0, column=0, sticky="nsew", pady=(0, 12))
            if show_cmd:
                self.right.grid(row=1, column=0, sticky="nsew", pady=(0, 12))
                self.stats.grid_remove()
                self.cmd_panel.grid(row=1, column=0, sticky="nsew")
            self.cmd_toggle.grid(**self._foot_toggle_grid)
            self.cmd_toggle.set_text("Hide commands" if show_cmd else "Show commands")

    def _toggle_commands(self):
        self._cmd_user_set = True
        self._cmd_visible = not self._cmd_visible
        self._layout_key = None
        self._apply_responsive()

    # ---- State + actions ----------------------------------------------------
    def _set_status(self, text, color):
        self.status_chip.set(text, color)

    def _on_mode_change(self, mode):
        self._mode = mode
        self.direction.set(mode)
        color = self._accent_color()
        for s in self.sliders.values():
            s.set_accent(color)
        for _, btn in self.preset_btns:
            btn.set_accent(color)
        self.apply_btn.set_accent(color)
        self.cmd_box.set_border_color(color)
        suffix = "IFB" if IS_LINUX else "dnctl"
        self.dir_desc.config(
            fg=color,
            text={MODE_EGRESS:  "Slows outgoing traffic only",
                  MODE_INGRESS: "Slows incoming traffic only",
                  MODE_BOTH:    f"Slows traffic both ways (via {suffix})"}[mode])
        self._update()

    def _on_slider(self):
        for _, btn in self.preset_btns:
            btn.set_active(False)
        self._update()

    def _update(self, _=None):
        iface   = self.iface_var.get()
        delay   = self.sliders["delay"].get()
        jitter  = self.sliders["jitter"].get()
        loss    = self.sliders["loss"].get()
        bw      = self.sliders["bw"].get()
        corrupt = self.sliders["corrupt"].get()
        dup     = self.sliders["dup"].get()

        if jitter > 0 and delay == 0:
            self.sliders["jitter"].set_note("  (needs latency)", ORANGE)
        else:
            self.sliders["jitter"].set_note("", FG)

        self.stat_vars["delay"].set(f"{delay} ms")
        self.stat_vars["loss"].set(f"{loss}%")
        self.stat_vars["bw"].set(bw_label(bw))
        self.stat_vars["jitter"].set(f"{jitter} ms")

        self._current_cmd = build_command(iface, delay, jitter, loss, bw,
                                          corrupt, dup, self._mode)
        self.cmd_box.set_text(self._current_cmd)

    def _apply_preset(self, name, vals):
        for n, btn in self.preset_btns:
            btn.set_active(n == name)
        for key, val in vals.items():
            if IS_MAC and key in ("corrupt", "dup"):
                val = 0
            self.sliders[key].set(val)
        self._update()

    def _copy(self):
        self.clipboard_clear()
        self.clipboard_append(self._current_cmd)
        self.copy_btn.set_text("Copied")
        self.after(1800, lambda: self.copy_btn.set_text("Copy command"))

    def _apply(self):
        if self._busy:
            return
        self._busy = True
        self.apply_btn.set_text("Applying...")
        self.apply_btn.set_enabled(False)
        self._set_status("Applying", ORANGE)
        self.cmd_box.clear_highlights()
        self.update()

        def on_line(line, state):
            if state == "running":
                self.cmd_box.highlight_line(line)
            elif state == "ok":
                self.cmd_box.mark_done(line, success=True)
            elif state == "error":
                self.cmd_box.mark_done(line, success=False)
            self.update_idletasks()

        iface = self.iface_var.get()
        try:
            ok, sentinel = run_tc(self._current_cmd, iface, self._mode, on_line=on_line)
        except Exception as e:
            ok, sentinel = False, str(e)

        self._busy = False
        self.apply_btn.set_text("Apply to system")
        self.apply_btn.set_enabled(True)

        if ok and sentinel == "cleared":
            self._active = False
            self._set_status("Inactive", FG2)
        elif ok:
            self._active = True
            self._set_status(f"Active on {iface} ({self.MODE_NAMES[self._mode]})", GREEN)
        else:
            self._set_status("Failed", RED)
            messagebox.showerror("Could not apply conditions",
                                 f"The system rejected the command:\n\n{sentinel}")

    def _reset(self):
        for _, btn in self.preset_btns:
            btn.set_active(False)
        for s in self.sliders.values():
            s.set(0)
        self._update()
        force_clear(self.iface_var.get())
        self._active = False
        self._set_status("Inactive", FG2)

    def _on_close(self):
        if self._active:
            ans = messagebox.askyesnocancel(
                "Conditions still active",
                "Network conditions are still applied.\n\n"
                "Remove them before closing?")
            if ans is None:
                return
            if ans:
                try:
                    force_clear(self.iface_var.get())
                except Exception:
                    pass
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()