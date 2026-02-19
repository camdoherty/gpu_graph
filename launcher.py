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
from dataclasses import dataclass

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_PYTHON = os.path.join(PROJECT_DIR, "venv", "bin", "python3")

# Keep launcher dependency behavior consistent when invoked as ./launcher.py.
if os.path.exists(VENV_PYTHON) and os.path.abspath(sys.executable) != os.path.abspath(VENV_PYTHON):
    os.execv(VENV_PYTHON, [VENV_PYTHON, __file__, *sys.argv[1:]])

PYTHON = VENV_PYTHON if os.path.exists(VENV_PYTHON) else sys.executable
HOLD_CMD = "sh -lc 'while :; do sleep 3600; done'"
BLANK_CMD = "sh -lc 'printf \"\\033[2J\\033[H\"; while :; do sleep 3600; done'"

# Import all monitor modules so they register themselves.
import muxmon
import muxmon.cpu
import muxmon.gpu
import muxmon.memory
import muxmon.net
import muxmon.storage

GRID_LAYOUTS = {
    "grid",
    "auto",
    "auto-square",
    "auto-wide",
    "auto-tall",
    "square",
    "wide",
    "tall",
}


@dataclass(frozen=True)
class PaneInfo:
    pane_id: str
    top: int
    left: int


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


def _attach_or_switch(session: str) -> None:
    if "TMUX" in os.environ:
        os.execvp("tmux", ["tmux", "switch-client", "-t", session])
    os.execvp("tmux", ["tmux", "attach-session", "-t", session])


def _setw_all(session: str, option: str, value: str) -> None:
    _tmux(["set-window-option", "-t", f"{session}:*", option, value])


def _apply_session_options(session: str, pane_borders: bool, active_pane_highlight: bool) -> None:
    _tmux(["set-option", "-t", session, "status", "off"])
    _tmux(["set-option", "-t", session, "window-size", "latest"])
    _setw_all(session, "aggressive-resize", "on")
    _setw_all(session, "pane-border-status", "off")
    _setw_all(session, "pane-border-lines", "single")
    if pane_borders:
        _setw_all(session, "pane-border-style", "fg=colour235")
        if active_pane_highlight:
            _setw_all(session, "pane-active-border-style", "fg=cyan")
            _setw_all(session, "pane-border-indicators", "colour")
        else:
            _setw_all(session, "pane-active-border-style", "fg=colour235")
            _setw_all(session, "pane-border-indicators", "off")
    else:
        # tmux (3.3a) cannot disable pane separator drawing, so use a low-contrast color.
        _setw_all(session, "pane-border-style", "fg=black")
        _setw_all(session, "pane-active-border-style", "fg=black")
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


def _plan_grid_dims(count: int, layout: str, term_cols: int, term_rows: int) -> tuple[int, int]:
    term_aspect = term_cols / max(1.0, float(term_rows))
    target_ratio = _target_col_row_ratio(layout, term_aspect)
    best: tuple[int, int, float, int] | None = None

    for cols in range(1, count + 1):
        rows = math.ceil(count / cols)
        empties = rows * cols - count
        ratio = cols / rows
        ratio_penalty = abs(math.log(max(0.01, ratio) / target_ratio))
        empty_penalty = empties * 0.12
        line_penalty = 1.0 if count >= 4 and (rows == 1 or cols == 1) else 0.0
        score = ratio_penalty + empty_penalty + line_penalty

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
    _apply_session_options(session, pane_borders, active_pane_highlight)

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
    extra_args: list[str],
    term_cols: int,
    term_rows: int,
    pad_empty: bool,
) -> None:
    cols, rows = _plan_grid_dims(len(monitors), layout, term_cols, term_rows)
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
    _apply_session_options(session, pane_borders, active_pane_highlight)

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
    pad_empty: bool,
    extra_args: list[str],
) -> None:
    if _session_exists(session):
        _apply_session_options(session, pane_borders, active_pane_highlight)
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

    _attach_or_switch(session)


def _monitor_cmd(name: str, extra_args: list[str]) -> str:
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
        "--layout",
        choices=[
            "vertical",
            "horizontal",
            "tiled",
            "grid",
            "auto",
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
        "--pane-borders",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Show tmux pane borders between monitors (default: on).",
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

    if args.list_monitors:
        print("Available monitors:")
        list_monitors()
        sys.exit(0)

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
        pad_empty=args.pad_empty,
        extra_args=extra_args,
    )


if __name__ == "__main__":
    main()
