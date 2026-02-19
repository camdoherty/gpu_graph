# Terminal System Monitor

Minimal, frameless terminal charts for GPU and network activity using braille unicode characters via [plotext](https://github.com/piccolomo/plotext).

## Scripts

| Script | Monitors | Colors |
|---|---|---|
| `gpu_terminal_graph.py` | GPU utilization %, VRAM usage % | cyan, magenta |
| `net_terminal_graph.py` | Download rate, upload rate | green, yellow |
| `stb_netacct_terminal_graph.py` | StB external download/upload from netacct JSON counters | green, yellow |

## Quick Start

```bash
# Run individually
./venv/bin/python3 gpu_terminal_graph.py
./venv/bin/python3 net_terminal_graph.py
./venv/bin/python3 stb_netacct_terminal_graph.py

# Or use combined tmux launchers
./install_launchers.sh
stb_mon_gpu_net
./mon_gpu_net_all.sh
```

Press `Ctrl+C` to exit. The tmux launcher reattaches if the session already exists.

## Launchers

- `stb_mon_gpu_net`
  - GPU + `stb_netacct_terminal_graph.py` (StB external net only)
  - Runs `stb_netacct_preflight.sh --fix` before tmux attach/new
- `mon_gpu_net_all.sh`
  - GPU + `net_terminal_graph.py` (host-wide non-loopback net)
- `mon_gpu_net`
  - Backward-compatible wrapper to `stb_mon_gpu_net`
- `gpu_net_mon.sh`
  - Legacy wrapper to `mon_gpu_net_all.sh` (not installed by default)
- tmux status bar is hidden for clean chart rendering

## Design

- **Frameless** — no borders, axes, ticks, or grid. Just braille data lines and a centered label.
- **Flicker-free** — double-buffered rendering via `plt.build()` + ANSI cursor-home (`\033[H`).
- **Instant Resize** — handles `SIGWINCH` signal to redraw instantly on terminal resize.
- **Resize-safe** — clears leftover remnants on terminal resize (`\033[J`).
- **Drift-free** — deadline-based timer loop instead of naive `sleep()`.
- **GPU**: queries NVML C library directly via `pynvml` (no `nvidia-smi` subprocess).
- **Network**: reads `/proc/net/dev` counters, computes rates, auto-scales units (`B/s` → `KB/s` → `MB/s` → `GB/s`).

## Configuration

| Constant | Default | Description |
|---|---|---|
| `INTERVAL_S` | `0.5` | Update interval in seconds |
| `WINDOW_SECONDS` | `60` | Rolling history window |
| `GPU_INDEX` | `0` | Which GPU to query (GPU script only) |

### `stb_netacct_terminal_graph.py`

- Expects counters JSON at `/run/stb-netacct/counters.json` by default.
- JSON keys expected by default:
  - `rx_bytes_total`
  - `tx_bytes_total`
- Override via flags:
  - `--counters-file`
  - `--rx-key`
  - `--tx-key`

## Dependencies

Managed via `venv` at `./venv/`:

- `plotext` — terminal plotting
- `nvidia-ml-py` — NVML GPU queries

## StB External Graph (End-To-End)

`stb_netacct_terminal_graph.py` expects root-side counters at `/run/stb-netacct/counters.json`.

Root-side bundle is included in `stb_netacct/` and installs:

- iptables/ip6tables cgroup+connmark rules for StB services
- exporter daemon that writes byte totals JSON at 0.5s cadence
- `stb-netacct.service` unit

Install root-side components:

```bash
sudo ./stb_netacct/install_root.sh
sudo systemctl enable --now stb-netacct.service
```

Then run the graph:

```bash
./venv/bin/python3 stb_netacct_terminal_graph.py
```

## Testing without a GPU

```bash
MOCK_MODE=1 ./venv/bin/python3 gpu_terminal_graph.py
```
