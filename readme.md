# Network Conditioner

A desktop GUI for simulating bad network conditions — latency, jitter, packet loss, bandwidth limits, corruption, and duplication — on a real network interface. Useful for testing how apps, websites, or games behave on slow or unreliable connections (2G, flaky Wi-Fi, high-latency links, etc.) without needing separate hardware or a proxy.

Built with Python and Tkinter. Two versions are included:

- **Linux-only** build using `tc` (Linux Traffic Control / `iproute2`), with full bidirectional support via IFB (Intermediate Functional Block) devices.
- **Cross-platform** build (Linux + macOS) that uses `tc` on Linux and `dnctl`/`pfctl` (Dummynet) on macOS, with a responsive UI that adapts to window size.

## Features

- 🎚 **Live sliders** for latency, jitter, packet loss, bandwidth limit, corruption, and duplication
- ↕ **Direction control** — apply conditions to egress (outgoing), ingress (incoming), or both
- ⚡ **One-click presets** — No limit, 2G, 3G, 4G, Slow Wi-Fi, Fast Wi-Fi, Flaky, High latency
- 📋 **Live command preview** showing the exact `tc` / `dnctl` commands that will run, with per-line execution status (running / success / error)
- 📎 **Copy command** to clipboard for use in scripts or documentation
- 🔄 **Reset / clear** button to instantly remove all applied conditions
- 🖥 Dark-themed, custom-drawn UI (rounded buttons, sliders, segmented controls — no OS-default widget chrome)
- 📱 Responsive layout (cross-platform version) that reflows into a single column on narrow/short windows

## How it works

### Linux
Conditions are applied with the `tc` (traffic control) command from `iproute2`:
- **Egress** traffic is shaped directly on the chosen interface using `netem` (delay/jitter/loss/corrupt/duplicate) and optionally `htb`/`tbf` for bandwidth limiting.
- **Ingress** traffic can't be shaped directly, so it's redirected through a virtual **IFB** interface (`ifb0`) using `tc filter ... mirred egress redirect`, then shaped there the same way.
- **Both** directions apply both sets of rules simultaneously.

### macOS
Conditions are applied with **Dummynet**, via `dnctl` (pipe configuration) and `pfctl` (firewall rules that route traffic through the pipes):
- Pipe 1 = egress, Pipe 2 = ingress.
- Note: Dummynet does not support per-packet corruption or duplication, and has no built-in Gaussian jitter (fixed delay only) — the UI disables/annotates these controls accordingly.

The app never hides what it's doing — the generated shell commands are always visible in the command preview panel before (and while) they run.

## Requirements

### Linux
- Python 3 with Tkinter: `sudo apt install python3-tk`
- `iproute2`: `sudo apt install iproute2`
- `sudo` privileges (required to run `tc`/`ip` commands)
- Kernel modules: `ifb`, `act_mirred`, `cls_matchall` (loaded automatically via `modprobe` when needed)

### macOS
- Python 3 with Tkinter: `brew install python-tk` (or use the official python.org installer, which bundles Tk)
- `pfctl` / `dnctl` — built in on macOS 10.7+
- `sudo` privileges

## Installation

```bash
git clone https://github.com/Anbarasu-A-N/network_conditioner.git
cd network-conditioner
```

No external Python packages are required — only the standard library (`tkinter`, `subprocess`, etc.).

## Usage

**Cross-platform version** (recommended — supports Linux & macOS, responsive UI):

```bash
sudo python3 network_conditioner.py
```

**Linux-only version**:

```bash
sudo python3 network_conditioner_linux.py
```

> `sudo` is required because shaping traffic requires elevated privileges. The cross-platform script checks for root and required dependencies on startup and will prompt you if something is missing.

### Basic workflow

1. Select the network **interface** you want to affect (e.g. `eth0`, `wlan0`, `en0`).
2. Choose a **direction**: Egress, Both, or Ingress.
3. Pick a **preset** or drag the sliders to craft custom conditions.
4. Review the **generated commands** in the preview panel.
5. Click **Apply to system** to activate the conditions, or **Copy command** to grab them for use elsewhere.
6. Click **Reset** (or close the app and choose to clean up) to remove all applied conditions.

## ⚠️ Warnings

- This tool modifies real kernel networking state (`tc` qdiscs on Linux, Dummynet pipes/pf rules on macOS). Conditions persist after the app closes unless you reset them first — the app will prompt you on exit if conditions are still active.
- Applying conditions to the interface you're using for an SSH session can disconnect you. Test on a local machine or a non-critical interface/VM first.
- Ingress shaping on Linux (via IFB) is more fragile than egress shaping — if you hit an error about an existing ingress qdisc, the app will attempt to force-clear it automatically; a manual fallback command is shown in the error message if that fails.
- Run `sudo tc qdisc del dev <iface> root` and `sudo tc qdisc del dev <iface> ingress` manually if you ever need to hard-reset Linux `tc` state outside the app.

## License

Free to use and modify for your own purposes. **Redistribution of this script
(original or modified) is not permitted** — don't re-upload it, repackage it, or
publish it elsewhere, including public forks. If you want to share it with someone,
please point them to this repository instead.

Provided "as is," with no warranty of any kind. Use at your own risk.