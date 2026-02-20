#!/usr/bin/env python3
"""muxmon launcher — compose monitors into tmux panes."""

from __future__ import annotations

import argparse
import math
import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_PYTHON = os.path.join(PROJECT_DIR, "venv", "bin", "python3")

# Keep launcher dependency behavior consistent when invoked as ./launcher.py.
if os.path.exists(VENV_PYTHON) and os.path.abspath(sys.executable) != os.path.abspath(VENV_PYTHON):
    os.execv(VENV_PYTHON, [VENV_PYTHON, __file__, *sys.argv[1:]])

PYTHON = VENV_PYTHON if os.path.exists(VENV_PYTHON) else sys.executable
HOLD_CMD = "sh -lc 'while :; do sleep 3600; done'"
BLANK_CMD = "sh -lc 'printf \"\\033[2J\\033[H\"; while :; do sleep 3600; done'"

muxmon = None

GRID_LAYOUTS = {
    "grid",
    "auto",
    "auto-geometry",
    "auto-square",
    "auto-wide",
    "auto-tall",
    "square",
    "wide",
    "tall",
}

AUTO_GEOMETRY_STACK_MAX_ASPECT_DEFAULT = 0.95
AUTO_GEOMETRY_TALL_MAX_ASPECT_DEFAULT = 1.25
AUTO_GEOMETRY_WIDE_MIN_ASPECT_DEFAULT = 2.40
LIVE_REFLOW_MIN_INTERVAL_MS_DEFAULT = 180


@dataclass(frozen=True)
class PaneInfo:
    pane_id: str
    top: int
    left: int


def _ensure_monitors_loaded() -> None:
    global muxmon
    if muxmon is not None:
        return

    import importlib

    muxmon = importlib.import_module("muxmon")
    importlib.import_module("muxmon.cpu")
    importlib.import_module("muxmon.gpu")
    importlib.import_module("muxmon.memory")
    importlib.import_module("muxmon.net")
    importlib.import_module("muxmon.storage")


def _tmux(args: list[str], *, capture_output: bool = False, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tmux", *args],
        check=check,
        text=True,
        capture_output=capture_output,
    )


def _session_exists(session: str) -> bool:
    result = subprocess.run(
        ["tmux", "has-session", "-t", session],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def _list_panes(session: str) -> list[PaneInfo]:
    result = _tmux(
        ["list-panes", "-t", session, "-F", "#{pane_id} #{pane_top} #{pane_left}"],
        capture_output=True,
    )
    panes = []
    for line in result.stdout.splitlines():
        pane_id, top, left = line.split()
        panes.append(PaneInfo(pane_id=pane_id, top=int(top), left=int(left)))
    return panes


def _pane_count(session: str, window: str = "0") -> int:
    result = _tmux(
        ["list-panes", "-t", f"{session}:{window}", "-F", "#{pane_id}"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return 0
    return len([line for line in result.stdout.splitlines() if line.strip()])


def _window_size(session: str, window: str = "0") -> tuple[int, int] | None:
    result = _tmux(
        ["display-message", "-p", "-t", f"{session}:{window}", "#{window_width} #{window_height}"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    parts = result.stdout.strip().split()
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def _reflow_stamp_path(session: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in session)
    return f"/tmp/muxmon-reflow-{safe}.stamp"


def _reflow_allowed(session: str, min_interval_ms: int) -> bool:
    if min_interval_ms <= 0:
        return True
    path = _reflow_stamp_path(session)
    now = time.time()
    try:
        with open(path) as f:
            prev = float(f.read().strip())
        if (now - prev) * 1000.0 < min_interval_ms:
            return False
    except Exception:
        pass
    try:
        with open(path, "w") as f:
            f.write(f"{now:.6f}")
    except Exception:
        pass
    return True


def _target_tmux_layout(
    *,
    layout: str,
    pane_count: int,
    term_cols: int,
    term_rows: int,
    auto_geometry_stack_max_aspect: float,
    auto_geometry_tall_max_aspect: float,
    auto_geometry_wide_min_aspect: float,
) -> str:
    normalized_layout = _normalize_layout(layout)
    if normalized_layout == "vertical":
        return "even-vertical"
    if normalized_layout == "horizontal":
        return "even-horizontal"
    if normalized_layout == "tiled":
        return "tiled"

    if normalized_layout in GRID_LAYOUTS:
        cols, rows = _plan_grid_dims(
            pane_count,
            normalized_layout,
            term_cols,
            term_rows,
            auto_geometry_stack_max_aspect=auto_geometry_stack_max_aspect,
            auto_geometry_tall_max_aspect=auto_geometry_tall_max_aspect,
            auto_geometry_wide_min_aspect=auto_geometry_wide_min_aspect,
        )
        if cols <= 1:
            return "even-vertical"
        if rows <= 1:
            return "even-horizontal"
        return "tiled"

    return "tiled"


def _apply_live_reflow(
    *,
    session: str,
    layout: str,
    auto_geometry_stack_max_aspect: float,
    auto_geometry_tall_max_aspect: float,
    auto_geometry_wide_min_aspect: float,
    min_interval_ms: int,
) -> None:
    if not _session_exists(session):
        return
    if not _reflow_allowed(session, min_interval_ms):
        return

    pane_count = _pane_count(session, window="0")
    if pane_count <= 1:
        return
    size = _window_size(session, window="0")
    if size is None:
        return
    term_cols, term_rows = size
    tmux_layout = _target_tmux_layout(
        layout=layout,
        pane_count=pane_count,
        term_cols=term_cols,
        term_rows=term_rows,
        auto_geometry_stack_max_aspect=auto_geometry_stack_max_aspect,
        auto_geometry_tall_max_aspect=auto_geometry_tall_max_aspect,
        auto_geometry_wide_min_aspect=auto_geometry_wide_min_aspect,
    )
    _tmux(["select-layout", "-t", f"{session}:0", tmux_layout], check=False)


def _configure_live_reflow_hook(
    *,
    session: str,
    enabled: bool,
    layout: str,
    auto_geometry_stack_max_aspect: float,
    auto_geometry_tall_max_aspect: float,
    auto_geometry_wide_min_aspect: float,
    min_interval_ms: int,
) -> None:
    hook_names = ("client-resized", "client-attached")
    if not enabled:
        for hook_name in hook_names:
            _tmux(["set-hook", "-u", "-t", session, hook_name], check=False)
        return

    cmd_argv = [
        PYTHON,
        __file__,
        "--internal-reflow",
        "--session",
        session,
        "--layout",
        layout,
        "--auto-geometry-stack-max-aspect",
        str(auto_geometry_stack_max_aspect),
        "--auto-geometry-tall-max-aspect",
        str(auto_geometry_tall_max_aspect),
        "--auto-geometry-wide-min-aspect",
        str(auto_geometry_wide_min_aspect),
        "--live-reflow-min-interval-ms",
        str(min_interval_ms),
    ]
    hook_script = " ".join(shlex.quote(arg) for arg in cmd_argv)
    hook_command = f"run-shell -b {shlex.quote(hook_script)}"
    for hook_name in hook_names:
        _tmux(["set-hook", "-t", session, hook_name, hook_command])


def _attach_or_switch(session: str) -> None:
    if "TMUX" in os.environ:
        os.execvp("tmux", ["tmux", "switch-client", "-t", session])
    os.execvp("tmux", ["tmux", "attach-session", "-t", session])


def _setw_all(session: str, option: str, value: str) -> None:
    _tmux(["set-window-option", "-t", f"{session}:*", option, value])


def _apply_session_options(
    session: str,
    pane_borders: bool,
    active_pane_highlight: bool,
    pane_border_color: str,
    pane_active_border_color: str,
    pane_muted_border_color: str,
) -> None:
    _tmux(["set-option", "-t", session, "status", "off"])
    _tmux(["set-option", "-t", session, "window-size", "latest"])
    _setw_all(session, "aggressive-resize", "on")
    _setw_all(session, "pane-border-status", "off")
    _setw_all(session, "pane-border-lines", "single")
    if pane_borders:
        _setw_all(session, "pane-border-style", f"fg={pane_border_color}")
        if active_pane_highlight:
            _setw_all(session, "pane-active-border-style", f"fg={pane_active_border_color}")
            _setw_all(session, "pane-border-indicators", "colour")
        else:
            _setw_all(session, "pane-active-border-style", f"fg={pane_border_color}")
            _setw_all(session, "pane-border-indicators", "off")
    else:
        # tmux (3.3a) cannot disable pane separator drawing, so use a low-contrast color.
        _setw_all(session, "pane-border-style", f"fg={pane_muted_border_color}")
        _setw_all(session, "pane-active-border-style", f"fg={pane_muted_border_color}")
        _setw_all(session, "pane-border-indicators", "off")


def _split_equal(*, session: str, target_pane: str, direction: str, parts: int) -> None:
    if parts <= 1:
        return
    for remaining in range(parts, 1, -1):
        pct = max(1, min(99, round(100 / remaining)))
        _tmux([
            "split-window",
            "-d",
            direction,
            "-p",
            str(pct),
            "-t",
            target_pane,
            "-c",
            PROJECT_DIR,
            HOLD_CMD,
        ])


def _normalize_layout(layout: str) -> str:
    aliases = {
        "grid": "square",
        "wide": "auto-wide",
        "tall": "auto-tall",
    }
    return aliases.get(layout, layout)


def _target_col_row_ratio(layout: str, term_aspect: float) -> float:
    if layout == "auto":
        return max(0.6, min(3.0, term_aspect))
    if layout in {"square", "auto-square"}:
        return 1.0
    if layout == "auto-wide":
        return max(1.2, min(4.0, term_aspect * 1.4))
    if layout == "auto-tall":
        return max(0.35, min(1.0, term_aspect * 0.7))
    return 1.0


def _plan_grid_dims(
    count: int,
    layout: str,
    term_cols: int,
    term_rows: int,
    *,
    pad_empty: bool = True,
    auto_geometry_stack_max_aspect: float = AUTO_GEOMETRY_STACK_MAX_ASPECT_DEFAULT,
    auto_geometry_tall_max_aspect: float = AUTO_GEOMETRY_TALL_MAX_ASPECT_DEFAULT,
    auto_geometry_wide_min_aspect: float = AUTO_GEOMETRY_WIDE_MIN_ASPECT_DEFAULT,
) -> tuple[int, int]:
    original_layout = layout
    term_aspect = term_cols / max(1.0, float(term_rows))
    if layout == "auto-geometry":
        if count >= 3 and term_aspect <= auto_geometry_stack_max_aspect:
            return 1, count
        if term_aspect >= auto_geometry_wide_min_aspect:
            layout = "auto-wide"
        elif term_aspect <= auto_geometry_tall_max_aspect:
            layout = "auto-tall"
        else:
            layout = "auto-square"

    target_ratio = _target_col_row_ratio(layout, term_aspect)
    best: tuple[int, int, float, int] | None = None

    for cols in range(1, count + 1):
        rows = math.ceil(count / cols)
        empties = rows * cols - count
        ratio = cols / rows
        ratio_penalty = abs(math.log(max(0.01, ratio) / target_ratio))
        empty_penalty = empties * 0.12
        line_penalty = 1.0 if count >= 4 and (rows == 1 or cols == 1) else 0.0
        imbalance_penalty = 0.0
        if not pad_empty:
            row_counts = _row_counts(count, rows, cols, pad_empty=False)
            imbalance_penalty = (max(row_counts) - min(row_counts)) * 0.22
        cell_shape_penalty = 0.0
        if original_layout == "auto-geometry":
            # Prefer panes that are at least slightly width-leaning rectangles.
            # cell_aspect = (cell_width / cell_height) in terminal character cells.
            cell_aspect = term_aspect * (rows / cols)
            if cell_aspect < 1.15:
                cell_shape_penalty = (1.15 - cell_aspect) * 1.2

        score = (
            ratio_penalty
            + empty_penalty
            + line_penalty
            + imbalance_penalty
            + cell_shape_penalty
        )

        candidate = (cols, rows, score, empties)
        if best is None:
            best = candidate
            continue
        _, _, best_score, best_empty = best
        if score < best_score or (score == best_score and empties < best_empty):
            best = candidate

    assert best is not None
    cols, rows, _, _ = best
    return cols, rows


def _row_counts(count: int, rows: int, cols: int, pad_empty: bool) -> list[int]:
    if pad_empty:
        return [cols] * rows
    remaining = count
    counts = []
    for _ in range(rows):
        take = min(cols, remaining)
        counts.append(take)
        remaining -= take
    return counts


def _launch_linear(
    *,
    monitors: list[str],
    session: str,
    layout: str,
    pane_borders: bool,
    active_pane_highlight: bool,
    pane_border_color: str,
    pane_active_border_color: str,
    pane_muted_border_color: str,
    extra_args: list[str],
    term_cols: int,
    term_rows: int,
) -> None:
    first, *rest = monitors
    _tmux([
        "new-session",
        "-d",
        "-x",
        str(term_cols),
        "-y",
        str(term_rows),
        "-s",
        session,
        "-c",
        PROJECT_DIR,
        _monitor_cmd(first, extra_args),
    ])
    _apply_session_options(
        session,
        pane_borders,
        active_pane_highlight,
        pane_border_color,
        pane_active_border_color,
        pane_muted_border_color,
    )

    for mon in rest:
        split_args = ["split-window", "-d", "-t", session, "-c", PROJECT_DIR]
        if layout == "vertical":
            split_args.append("-v")
        elif layout == "horizontal":
            split_args.append("-h")
        split_args.append(_monitor_cmd(mon, extra_args))
        _tmux(split_args)
        if layout == "tiled":
            _tmux(["select-layout", "-t", session, "tiled"])

    layout_map = {
        "vertical": "even-vertical",
        "horizontal": "even-horizontal",
        "tiled": "tiled",
    }
    _tmux(["select-layout", "-t", session, layout_map[layout]])
    _tmux(["select-pane", "-t", f"{session}:0.0"])


def _launch_grid(
    *,
    monitors: list[str],
    session: str,
    layout: str,
    pane_borders: bool,
    active_pane_highlight: bool,
    pane_border_color: str,
    pane_active_border_color: str,
    pane_muted_border_color: str,
    auto_geometry_stack_max_aspect: float,
    auto_geometry_tall_max_aspect: float,
    auto_geometry_wide_min_aspect: float,
    extra_args: list[str],
    term_cols: int,
    term_rows: int,
    pad_empty: bool,
) -> None:
    cols, rows = _plan_grid_dims(
        len(monitors),
        layout,
        term_cols,
        term_rows,
        pad_empty=pad_empty,
        auto_geometry_stack_max_aspect=auto_geometry_stack_max_aspect,
        auto_geometry_tall_max_aspect=auto_geometry_tall_max_aspect,
        auto_geometry_wide_min_aspect=auto_geometry_wide_min_aspect,
    )
    row_counts = _row_counts(len(monitors), rows, cols, pad_empty)
    total_slots = sum(row_counts)

    _tmux([
        "new-session",
        "-d",
        "-x",
        str(term_cols),
        "-y",
        str(term_rows),
        "-s",
        session,
        "-c",
        PROJECT_DIR,
        HOLD_CMD,
    ])
    _apply_session_options(
        session,
        pane_borders,
        active_pane_highlight,
        pane_border_color,
        pane_active_border_color,
        pane_muted_border_color,
    )

    root_pane = _list_panes(session)[0].pane_id
    _split_equal(session=session, target_pane=root_pane, direction="-v", parts=rows)

    row_roots = sorted(_list_panes(session), key=lambda p: (p.top, p.left))
    for row_root, row_size in zip(row_roots, row_counts):
        _split_equal(session=session, target_pane=row_root.pane_id, direction="-h", parts=row_size)

    panes = sorted(_list_panes(session), key=lambda p: (p.top, p.left))
    if len(panes) != total_slots:
        raise RuntimeError(f"Expected {total_slots} panes, got {len(panes)}")

    commands = [_monitor_cmd(mon, extra_args) for mon in monitors]
    if len(commands) < len(panes):
        commands.extend([BLANK_CMD] * (len(panes) - len(commands)))

    for pane, cmd in zip(panes, commands):
        _tmux(["respawn-pane", "-k", "-t", pane.pane_id, "-c", PROJECT_DIR, cmd])

    _tmux(["select-pane", "-t", panes[0].pane_id])


def list_monitors() -> None:
    _ensure_monitors_loaded()
    for name, cls in sorted(muxmon.REGISTRY.items()):
        aliases = [a for a, canon in muxmon.ALIASES.items() if canon == name]
        alias_str = f"  (aka {', '.join(aliases)})" if aliases else ""
        avail = "✓" if cls.is_available() else "✗"
        print(f"  {avail}  {name:12s}  {cls.default_title}{alias_str}")


def launch(
    monitors: list[str],
    session: str,
    layout: str,
    pane_borders: bool,
    active_pane_highlight: bool,
    pane_border_color: str,
    pane_active_border_color: str,
    pane_muted_border_color: str,
    auto_geometry_stack_max_aspect: float,
    auto_geometry_tall_max_aspect: float,
    auto_geometry_wide_min_aspect: float,
    live_reflow: bool,
    live_reflow_min_interval_ms: int,
    pad_empty: bool,
    extra_args: list[str],
) -> None:
    if _session_exists(session):
        _apply_session_options(
            session,
            pane_borders,
            active_pane_highlight,
            pane_border_color,
            pane_active_border_color,
            pane_muted_border_color,
        )
        _configure_live_reflow_hook(
            session=session,
            enabled=live_reflow,
            layout=layout,
            auto_geometry_stack_max_aspect=auto_geometry_stack_max_aspect,
            auto_geometry_tall_max_aspect=auto_geometry_tall_max_aspect,
            auto_geometry_wide_min_aspect=auto_geometry_wide_min_aspect,
            min_interval_ms=live_reflow_min_interval_ms,
        )
        if live_reflow:
            _apply_live_reflow(
                session=session,
                layout=layout,
                auto_geometry_stack_max_aspect=auto_geometry_stack_max_aspect,
                auto_geometry_tall_max_aspect=auto_geometry_tall_max_aspect,
                auto_geometry_wide_min_aspect=auto_geometry_wide_min_aspect,
                min_interval_ms=0,
            )
        _attach_or_switch(session)
        return

    if not monitors:
        print("No monitors specified.", file=sys.stderr)
        sys.exit(1)

    term_cols, term_rows = shutil.get_terminal_size()
    normalized_layout = _normalize_layout(layout)

    try:
        if normalized_layout in {"vertical", "horizontal", "tiled"}:
            _launch_linear(
                monitors=monitors,
                session=session,
                layout=normalized_layout,
                pane_borders=pane_borders,
                active_pane_highlight=active_pane_highlight,
                pane_border_color=pane_border_color,
                pane_active_border_color=pane_active_border_color,
                pane_muted_border_color=pane_muted_border_color,
                extra_args=extra_args,
                term_cols=term_cols,
                term_rows=term_rows,
            )
        elif normalized_layout in GRID_LAYOUTS:
            _launch_grid(
                monitors=monitors,
                session=session,
                layout=normalized_layout,
                pane_borders=pane_borders,
                active_pane_highlight=active_pane_highlight,
                pane_border_color=pane_border_color,
                pane_active_border_color=pane_active_border_color,
                pane_muted_border_color=pane_muted_border_color,
                auto_geometry_stack_max_aspect=auto_geometry_stack_max_aspect,
                auto_geometry_tall_max_aspect=auto_geometry_tall_max_aspect,
                auto_geometry_wide_min_aspect=auto_geometry_wide_min_aspect,
                extra_args=extra_args,
                term_cols=term_cols,
                term_rows=term_rows,
                pad_empty=pad_empty,
            )
        else:
            raise ValueError(f"Unsupported layout: {layout}")
    except Exception:
        _tmux(["kill-session", "-t", session], check=False)
        raise

    _configure_live_reflow_hook(
        session=session,
        enabled=live_reflow,
        layout=layout,
        auto_geometry_stack_max_aspect=auto_geometry_stack_max_aspect,
        auto_geometry_tall_max_aspect=auto_geometry_tall_max_aspect,
        auto_geometry_wide_min_aspect=auto_geometry_wide_min_aspect,
        min_interval_ms=live_reflow_min_interval_ms,
    )
    if live_reflow:
        _apply_live_reflow(
            session=session,
            layout=layout,
            auto_geometry_stack_max_aspect=auto_geometry_stack_max_aspect,
            auto_geometry_tall_max_aspect=auto_geometry_tall_max_aspect,
            auto_geometry_wide_min_aspect=auto_geometry_wide_min_aspect,
            min_interval_ms=0,
        )

    _attach_or_switch(session)


def _monitor_cmd(name: str, extra_args: list[str]) -> str:
    _ensure_monitors_loaded()
    canonical = muxmon.resolve(name)
    parts = [PYTHON, "-m", f"muxmon.{canonical}", *extra_args]
    return " ".join(shlex.quote(part) for part in parts)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Launch terminal monitors in tmux panes.",
        epilog="Monitor-specific flags (e.g. --mock, --per-core) can be passed after '--'.",
    )
    parser.add_argument(
        "monitors",
        nargs="*",
        help="Monitor names to launch (e.g. cpu gpu net)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Launch all available monitors",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_monitors",
        help="List available monitors and exit",
    )
    parser.add_argument(
        "--session",
        default="muxmon",
        help="tmux session name (default: muxmon)",
    )
    parser.add_argument(
        "--internal-reflow",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--layout",
        choices=[
            "vertical",
            "horizontal",
            "tiled",
            "grid",
            "auto",
            "auto-geometry",
            "auto-square",
            "auto-wide",
            "auto-tall",
            "square",
            "wide",
            "tall",
        ],
        default="auto",
        help=(
            "Pane layout. auto modes build an NxN-style grid "
            "(default: auto)."
        ),
    )
    parser.add_argument(
        "--auto-geometry-stack-max-aspect",
        type=float,
        default=AUTO_GEOMETRY_STACK_MAX_ASPECT_DEFAULT,
        help=(
            "For --layout auto-geometry, aspect ratio (cols/rows) at or below this "
            "stacks panes as 1xN (default: 0.95)."
        ),
    )
    parser.add_argument(
        "--auto-geometry-tall-max-aspect",
        type=float,
        default=AUTO_GEOMETRY_TALL_MAX_ASPECT_DEFAULT,
        help=(
            "For --layout auto-geometry, aspect ratio (cols/rows) at or below this "
            "uses tall-biased grid (default: 1.25)."
        ),
    )
    parser.add_argument(
        "--auto-geometry-wide-min-aspect",
        type=float,
        default=AUTO_GEOMETRY_WIDE_MIN_ASPECT_DEFAULT,
        help=(
            "For --layout auto-geometry, aspect ratio (cols/rows) at or above this "
            "uses wide-biased grid (default: 2.40)."
        ),
    )
    parser.add_argument(
        "--live-reflow",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Reflow pane layout on tmux client resize using lightweight "
            "select-layout operations (default: off)."
        ),
    )
    parser.add_argument(
        "--live-reflow-min-interval-ms",
        type=int,
        default=LIVE_REFLOW_MIN_INTERVAL_MS_DEFAULT,
        help=(
            "Minimum interval between live reflow events in milliseconds "
            f"(default: {LIVE_REFLOW_MIN_INTERVAL_MS_DEFAULT})."
        ),
    )
    parser.add_argument(
        "--pane-borders",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Show tmux pane borders between monitors (default: on).",
    )
    parser.add_argument(
        "--pane-border-color",
        default="colour235",
        help="Border color when pane borders are enabled (default: colour235).",
    )
    parser.add_argument(
        "--pane-active-border-color",
        default="cyan",
        help="Active pane border color when active highlight is enabled (default: cyan).",
    )
    parser.add_argument(
        "--pane-muted-border-color",
        default="black",
        help="Low-contrast border color used by --no-pane-borders (default: black).",
    )
    parser.add_argument(
        "--active-pane-highlight",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Use a distinct border color for the active pane (default: off).",
    )
    parser.add_argument(
        "--pad-empty",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Pad auto grid with blank panes for uniform cell sizes (default: on).",
    )

    argv = sys.argv[1:]
    if "--" in argv:
        idx = argv.index("--")
        launcher_argv = argv[:idx]
        extra_args = argv[idx + 1:]
    else:
        launcher_argv = argv
        extra_args = []

    args = parser.parse_args(launcher_argv)

    if args.auto_geometry_stack_max_aspect <= 0:
        parser.error("--auto-geometry-stack-max-aspect must be > 0")
    if args.auto_geometry_tall_max_aspect <= 0:
        parser.error("--auto-geometry-tall-max-aspect must be > 0")
    if args.auto_geometry_wide_min_aspect <= 0:
        parser.error("--auto-geometry-wide-min-aspect must be > 0")
    if args.auto_geometry_stack_max_aspect > args.auto_geometry_tall_max_aspect:
        parser.error(
            "--auto-geometry-stack-max-aspect must be <= "
            "--auto-geometry-tall-max-aspect"
        )
    if args.auto_geometry_tall_max_aspect > args.auto_geometry_wide_min_aspect:
        parser.error(
            "--auto-geometry-tall-max-aspect must be <= "
            "--auto-geometry-wide-min-aspect"
        )
    if args.live_reflow_min_interval_ms < 0:
        parser.error("--live-reflow-min-interval-ms must be >= 0")

    if args.internal_reflow:
        _apply_live_reflow(
            session=args.session,
            layout=args.layout,
            auto_geometry_stack_max_aspect=args.auto_geometry_stack_max_aspect,
            auto_geometry_tall_max_aspect=args.auto_geometry_tall_max_aspect,
            auto_geometry_wide_min_aspect=args.auto_geometry_wide_min_aspect,
            min_interval_ms=args.live_reflow_min_interval_ms,
        )
        return

    if args.list_monitors:
        print("Available monitors:")
        list_monitors()
        sys.exit(0)

    _ensure_monitors_loaded()

    if args.all:
        monitors = [name for name, cls in sorted(muxmon.REGISTRY.items()) if cls.is_available()]
    else:
        monitors = args.monitors

    if not monitors:
        parser.print_help()
        sys.exit(1)

    resolved = []
    for name in monitors:
        canonical = muxmon.resolve(name)
        if canonical not in muxmon.REGISTRY:
            all_names = sorted(set(list(muxmon.REGISTRY) + list(muxmon.ALIASES)))
            print(f"Unknown monitor: {name}", file=sys.stderr)
            print(f"Available: {', '.join(all_names)}", file=sys.stderr)
            sys.exit(1)
        if not muxmon.REGISTRY[canonical].is_available():
            print(f"Monitor '{canonical}' is not available on this system.", file=sys.stderr)
            sys.exit(1)
        resolved.append(canonical)

    launch(
        monitors=resolved,
        session=args.session,
        layout=args.layout,
        pane_borders=args.pane_borders,
        active_pane_highlight=args.active_pane_highlight,
        pane_border_color=args.pane_border_color,
        pane_active_border_color=args.pane_active_border_color,
        pane_muted_border_color=args.pane_muted_border_color,
        auto_geometry_stack_max_aspect=args.auto_geometry_stack_max_aspect,
        auto_geometry_tall_max_aspect=args.auto_geometry_tall_max_aspect,
        auto_geometry_wide_min_aspect=args.auto_geometry_wide_min_aspect,
        live_reflow=args.live_reflow,
        live_reflow_min_interval_ms=args.live_reflow_min_interval_ms,
        pad_empty=args.pad_empty,
        extra_args=extra_args,
    )


if __name__ == "__main__":
    main()
