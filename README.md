# Terminal System Monitor

Minimal, frameless terminal charts for GPU and network activity using braille unicode characters via [plotext](https://github.com/piccolomo/plotext).

## Scripts

| Script | Monitors | Colors |
|---|---|---|
| `gpu_terminal_graph.py` | GPU utilization %, VRAM usage % | cyan, magenta |
| `net_terminal_graph.py` | Download rate, upload rate | green, yellow |

## Quick Start

```bash
# Run individually
./venv/bin/python3 gpu_terminal_graph.py
./venv/bin/python3 net_terminal_graph.py

# Or use the combined tmux launcher (available globally)
mon_gpu_net
```

Press `Ctrl+C` to exit. The tmux launcher reattaches if the session already exists.

## `mon_gpu_net` Launcher

- **Location**: `/home/cad/dev/gpu_graph/mon_gpu_net` → `~/.local/bin/mon_gpu_net`
- **Layout**: GPU on top, Network on bottom (tmux split)
- **tmux status bar**: hidden for a clean look

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

## Dependencies

Managed via `venv` at `./venv/`:

- `plotext` — terminal plotting
- `nvidia-ml-py` — NVML GPU queries

## Testing without a GPU

```bash
MOCK_MODE=1 ./venv/bin/python3 gpu_terminal_graph.py
```
