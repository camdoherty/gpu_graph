# muxmon

`muxmon` launches monitor modules into a `tmux` session and arranges them in adaptive pane layouts.

```bash
# Useful examples (copy/paste)

# 1) List monitors
/home/cad/dev/gpu_graph/launcher.py --list

# 2) Default adaptive geometry for all available monitors
/home/cad/dev/gpu_graph/launcher.py --all --layout auto-geometry

# GOOD ALL 3) Preferred no-frame dashboard with live resize reflow 
/home/cad/dev/gpu_graph/launcher.py --all --layout auto-geometry --live-reflow --no-pad-empty --pane-borders --no-active-pane-highlight -- --no-frame

# 4) Same as above, but low render overhead
/home/cad/dev/gpu_graph/launcher.py --all --layout auto-geometry --live-reflow --no-pad-empty -- --no-frame --interval 0.5 --draw-interval 1.0

# 5) All borders ON (tmux separators + chart frames)
/home/cad/dev/gpu_graph/launcher.py --all --pane-borders -- --frame

# 6) All borders OFF (muted tmux separators + no chart frame)
/home/cad/dev/gpu_graph/launcher.py --all --no-pane-borders -- --no-frame

# 7) Custom pane border colors
/home/cad/dev/gpu_graph/launcher.py --all --pane-borders --pane-border-color colour240 --pane-active-border-color colour45 --active-pane-highlight

# 8) Custom chart colors
/home/cad/dev/gpu_graph/launcher.py --all -- --frame --title-color white --axes-color colour240 --ticks-color colour240 --canvas-color black --series-colors cyan,magenta,green,yellow

# 9) Recreate session after layout flag changes
tmux kill-session -t muxmon
/home/cad/dev/gpu_graph/launcher.py --all --layout auto-geometry
```

## Goal

Create a reliable adaptive grid that keeps panes readable across terminal sizes.

Contextual restatement of the current requirement:

> In `auto-geometry`, prefer balanced rectangular panes (wider than tall) and switch to multi-row grids earlier, instead of staying in a single long row until the terminal is very narrow.

## Quick Start

```bash
# List available monitors
/home/cad/dev/gpu_graph/launcher.py --list

# Launch all available monitors in an adaptive grid
/home/cad/dev/gpu_graph/launcher.py --all --layout auto-geometry

# Current preferred command style
/home/cad/dev/gpu_graph/launcher.py cpu cpu mem net --layout auto-geometry --live-reflow --no-pad-empty -- --no-frame
```

## Border Controls

Two independent border systems exist:

- `tmux` pane separators (between panes)
- Monitor plot frame (inside each chart)

```bash
# All on
/home/cad/dev/gpu_graph/launcher.py --all --pane-borders -- --frame

# All off
/home/cad/dev/gpu_graph/launcher.py --all --no-pane-borders -- --no-frame
```

Notes:

- `--pane-borders` is a launcher flag.
- `--frame` is a monitor flag passed after `--`.
- On tmux 3.3a, pane separators cannot be fully disabled; `--no-pane-borders` uses a muted color.

## Layout Modes

`--layout` supports:

- Linear: `vertical`, `horizontal`, `tiled`
- Adaptive/grid: `auto`, `auto-geometry`, `auto-square`, `auto-wide`, `auto-tall`
- Aliases: `grid -> square`, `wide -> auto-wide`, `tall -> auto-tall`

### `auto-geometry` thresholds

`auto-geometry` uses terminal aspect ratio (`terminal_cols / terminal_rows`, in character cells):

- `--auto-geometry-stack-max-aspect` (default `0.95`): at/below this, force `1xN` vertical stack
- `--auto-geometry-tall-max-aspect` (default `1.25`): at/below this, bias toward taller grids
- `--auto-geometry-wide-min-aspect` (default `2.40`): at/above this, bias toward wider grids

Additionally, `auto-geometry` now penalizes pane shapes that are too tall/narrow, so it prefers width-leaning rectangular cells and moves to multi-row layouts earlier.

## Live Reflow

```bash
/home/cad/dev/gpu_graph/launcher.py --all --layout auto-geometry --live-reflow
```

- Hooks `client-resized` and `client-attached`
- Uses lightweight `tmux select-layout` only (no pane respawn)
- Debounced via `--live-reflow-min-interval-ms` (default `180`)

Tradeoff: complex grids during live reflow are approximate because processes are not restarted.

## Color Customization

### Pane border colors

```bash
/home/cad/dev/gpu_graph/launcher.py --all \
  --pane-borders \
  --pane-border-color colour240 \
  --pane-active-border-color colour45 \
  --active-pane-highlight
```

### Chart palette (shared monitor flags)

```bash
/home/cad/dev/gpu_graph/launcher.py --all -- --frame \
  --title-color white \
  --axes-color colour240 \
  --ticks-color colour240 \
  --canvas-color black \
  --series-colors cyan,magenta,green,yellow
```

### Per-series override

```bash
/home/cad/dev/gpu_graph/launcher.py cpu -- --series-color usr=green --series-color sys=yellow
```

## CLI Reference

### Launcher flags

- `--all`: launch all available monitors
- `--list`: list available monitors and exit
- `--session <name>`: tmux session name (default `muxmon`)
- `--layout <mode>`: pane layout mode
- `--pad-empty / --no-pad-empty`: pad grid with blank panes or not
- `--pane-borders / --no-pane-borders`
- `--pane-border-color <color>`
- `--pane-active-border-color <color>`
- `--pane-muted-border-color <color>`
- `--active-pane-highlight / --no-active-pane-highlight`
- `--auto-geometry-stack-max-aspect <float>`
- `--auto-geometry-tall-max-aspect <float>`
- `--auto-geometry-wide-min-aspect <float>`
- `--live-reflow / --no-live-reflow`
- `--live-reflow-min-interval-ms <int>`

### Shared monitor flags (`--` pass-through)

- `--interval <seconds>`
- `--draw-interval <seconds>` (defaults to `--interval`)
- `--window <seconds>`
- `--title <text>`
- `--no-legend`
- `--frame / --no-frame`
- `--title-color <color>`
- `--axes-color <color>`
- `--ticks-color <color>`
- `--canvas-color <color>`
- `--series-colors c1,c2,...`
- `--series-color name=color` (repeatable)

## Available Monitors

Current registry names:

- `cpu`
- `gpu`
- `memory` (`mem`)
- `net`
- `storage` (`disk`, `io`)

Use `/home/cad/dev/gpu_graph/launcher.py --list` to verify runtime availability.

## Session Lifecycle

If the session already exists, launcher reuses it.
To apply new layout construction flags, recreate the session:

```bash
tmux kill-session -t muxmon
/home/cad/dev/gpu_graph/launcher.py --all --layout auto-geometry
```

## Troubleshooting

- CPU pane border color differs from others:
  - disable active highlight: `--no-active-pane-highlight`
- Borders still visible with `--no-pane-borders`:
  - tmux separators are muted, not removed, on tmux 3.3a
- Layout unchanged after flag changes:
  - kill/recreate session
