# muxmon

`muxmon` is the `tmux` launcher + plugin monitor system in this repo.
It is optimized for dense multi-pane terminal monitoring where every pane stays readable under resize.

## What It Solves

- Launch multiple monitors in one `tmux` session from one command.
- Build adaptive `NxN`-style grids for mixed terminal sizes.
- Keep monitor rendering frameless by default, with optional frame/border toggles.
- Reattach quickly to an existing monitor session.

## Quick Start

```bash
# Tested GOOD
./launcher.py 
/home/cad/dev/gpu_graph/launcher.py --all --layout auto-square  --no-pad-empty -- --no-frame
/home/cad/dev/gpu_graph/launcher.py --all --layout auto-square  --no-pad-empty --pane-borders -- --no-frame

```
/home/cad/dev/gpu_graph/launcher.py --all --layout auto-square  --no-pad-empty  ## CPU pane has different pane border color
/home/cad/dev/gpu_graph/launcher.py --all --layout auto-square  --no-pad-empty -- --frame ## correctly shows frames - CPU pane has different pane border color
/home/cad/dev/gpu_graph/launcher.py --all --layout auto-square  --no-pad-empty --pane-borders -- --no-frame ## CPU pane has different pane border color

tmux kill-session -t muxmon
/home/cad/dev/gpu_graph/launcher.py --all --layout auto-square --no-active-pane-highlight
/home/cad/dev/gpu_graph/launcher.py --all --layout auto-square --no-pad-empty --no-pane-borders --no-active-pane-highlight -- --no-frame
/home/cad/dev/gpu_graph/launcher.py --all --layout auto-square --no-pad-empty --no-pane-borders --no-active-pane-highlight -- --frame
./launcher.py --all --layout auto-square --no-active-pane-highlight

tmux kill-session -t muxmon
/home/cad/dev/gpu_graph/launcher.py  --all --layout auto-square --no-active-pane-highlight
tmux kill-session -t muxmon
/home/cad/dev/gpu_graph/launcher.py  --all --layout auto-square --no-pad-empty --pane-borders --no-active-pane-highlight -- --no-frame


```bash
# List monitors available on this host
./launcher.py --list

# Launch all available monitors in adaptive square-ish grid
./launcher.py --all --layout auto-square

# Your current "close to ideal" mode
./launcher.py --all --layout auto-square --no-pad-empty --no-pane-borders
```

## Border Controls (All On / All Off)

There are two independent border systems:

- `tmux` pane borders (between panes)
- chart frame border (inside each monitor plot)

### All borders ON

```bash
./launcher.py --all --pane-borders -- --frame
```

### All borders OFF

```bash
./launcher.py --all --no-pane-borders -- --no-frame
```

Notes:

- `--pane-borders/--no-pane-borders` are launcher flags.
- `--frame/--no-frame` are monitor flags passed through after `--`.
- Pass-through args are sent to every pane command. Use shared flags when launching mixed monitors.
- tmux 3.3a cannot fully disable pane separator drawing; `--no-pane-borders` uses a low-contrast border color.

## CLI Reference

### Launcher (`launcher.py`)

- `--all`: launch all monitors with `is_available() == True`.
- `--list`: print available monitors.
- `--session <name>`: tmux session name (default `muxmon`).
- `--layout <mode>`:
  - linear modes: `vertical`, `horizontal`, `tiled`
  - adaptive grid modes: `auto`, `auto-square`, `auto-wide`, `auto-tall`
  - aliases: `grid` -> `square`, `square`, `wide`, `tall`
- `--pad-empty / --no-pad-empty`:
  - `--pad-empty` fills unused grid slots with blank panes for uniform cell size.
  - `--no-pad-empty` creates only needed panes; last row can be wider.
- `--pane-borders / --no-pane-borders`: show/hide pane separators.
- `--active-pane-highlight / --no-active-pane-highlight`:
  - when enabled, active pane border uses accent color (`fg=cyan`)
  - when disabled (default), active pane border matches other panes

### Shared Monitor Flags (`muxmon/base.py`)

These apply to every monitor module:

- `--interval <seconds>` default `0.5`
- `--window <seconds>` default `60`
- `--title <text>`
- `--no-legend`
- `--frame / --no-frame` default `--no-frame`

## Available Monitors

From current registry:

- `cpu`
- `gpu`
- `memory` (alias: `mem`)
- `net`
- `storage` (aliases: `disk`, `io`)

Use `./launcher.py --list` to confirm host availability (`gpu` depends on NVML).

## Layout Behavior (Technical)

### 1) Grid size selection

For adaptive/grid layouts, launcher evaluates candidate `(cols, rows)` pairs and scores each:

- `rows = ceil(count / cols)`
- `empties = rows * cols - count`
- `ratio = cols / rows`
- `score = ratio_penalty + empty_penalty + line_penalty`

Where:

- `ratio_penalty = abs(log(ratio / target_ratio))`
- `empty_penalty = empties * 0.12`
- `line_penalty = 1.0` when monitor count >= 4 and layout collapses to 1 row or 1 column

Target ratio by mode:

- `auto`: follows terminal aspect ratio (clamped)
- `auto-square` / `square` / `grid`: target `1.0`
- `auto-wide` / `wide`: bias toward more columns
- `auto-tall` / `tall`: bias toward more rows

### 2) Pane construction

Grid launch flow:

1. Create detached session with one placeholder pane.
2. Split vertically into target row count.
3. Split each row horizontally into that row's target column count.
4. Sort panes by `(top, left)` for stable row-major assignment.
5. `respawn-pane` each slot with monitor command (or blank placeholder if padded).

This avoids `tmux tiled` surprises and gives deterministic placement.

### 3) Scaling/readability implications

- With `--pad-empty`, pane geometry is uniform across rows, which helps visual consistency and perceived scaling.
- With `--no-pad-empty`, no blank panes are created, but last-row panes can be larger.
- Chart y-scaling is per-monitor process in `BaseMonitor`; each pane scales independently by its series mode.

## Session Lifecycle

- If session exists, launcher attaches/switches immediately and does not rebuild layout.
- To apply a changed layout flag set, recreate session:

```bash
tmux kill-session -t muxmon
./launcher.py --all --layout auto-square
```

## Runtime/Dependency Notes

- `launcher.py` re-execs into `./venv/bin/python3` when available.
- Monitor pane commands run with quoted args (`shlex.quote`) and `PYTHONPATH` set to project root.
- `tmux` status line is disabled for cleaner chart panes.

## Troubleshooting

- Border still visible:
  - turn off both systems: `--no-pane-borders -- --no-frame`
  - ensure you started a new session after changing launcher flags
- First pane border color differs from others:
  - this is active-pane highlighting in tmux
  - use `--no-active-pane-highlight` for uniform pane borders
- Layout did not change:
  - kill existing session first (`tmux kill-session -t <session>`)
- Unknown monitor error:
  - check `./launcher.py --list`
  - verify alias spelling (`mem`, `disk`, `io`)
