#!/usr/bin/env python3
"""muxmon launcher — compose monitors into tmux panes.

Usage:
    ./launcher.py cpu gpu net          # 3 panes, vertical split
    ./launcher.py --all                # all viable monitors
    ./launcher.py --list               # print available monitors
    ./launcher.py --session muxmon     # custom session name
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

# Import all monitor modules so they register themselves
import muxmon
import muxmon.cpu
import muxmon.memory
import muxmon.storage
import muxmon.gpu
import muxmon.net

PYTHON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "venv", "bin", "python3")
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))


def list_monitors() -> None:
    for name, cls in sorted(muxmon.REGISTRY.items()):
        aliases = [a for a, canon in muxmon.ALIASES.items() if canon == name]
        alias_str = f"  (aka {', '.join(aliases)})" if aliases else ""
        avail = "✓" if cls.is_available() else "✗"
        print(f"  {avail}  {name:12s}  {cls.default_title}{alias_str}")


def launch(monitors: list[str], session: str, extra_args: list[str]) -> None:
    # Check if session already exists
    result = subprocess.run(
        ["tmux", "has-session", "-t", session],
        capture_output=True, check=False,
    )
    if result.returncode == 0:
        # Session exists — reattach
        os.execvp("tmux", ["tmux", "attach-session", "-t", session])
        return  # unreachable after exec

    if not monitors:
        print("No monitors specified.", file=sys.stderr)
        sys.exit(1)

    # Build tmux commands
    # First monitor becomes the initial window
    first, *rest = monitors
    first_cmd = _monitor_cmd(first, extra_args)

    tmux_args = [
        "tmux",
        "new-session", "-d", "-s", session, first_cmd, ";",
        "set-option", "-t", session, "status", "off", ";",
    ]

    for mon in rest:
        cmd = _monitor_cmd(mon, extra_args)
        tmux_args.extend(["split-window", "-v", cmd, ";"])

    # Even out the pane sizes
    tmux_args.extend(["select-layout", "-t", session, "even-vertical", ";"])
    # Select first pane
    tmux_args.extend(["select-pane", "-t", "0", ";"])
    # Attach
    tmux_args.extend(["attach-session", "-t", session])

    os.execvp("tmux", tmux_args)


def _monitor_cmd(name: str, extra_args: list[str]) -> str:
    """Build the shell command string for a monitor pane."""
    # Always use canonical name for the module path
    canonical = muxmon.resolve(name)
    parts = [PYTHON, "-m", f"muxmon.{canonical}"]
    parts.extend(extra_args)
    return " ".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Launch terminal monitors in tmux panes.",
        epilog="Monitor-specific flags (e.g. --mock, --per-core) can be passed after '--'.",
    )
    parser.add_argument("monitors", nargs="*",
                        help="Monitor names to launch (e.g. cpu gpu net)")
    parser.add_argument("--all", action="store_true",
                        help="Launch all available monitors")
    parser.add_argument("--list", action="store_true", dest="list_monitors",
                        help="List available monitors and exit")
    parser.add_argument("--session", default="muxmon",
                        help="tmux session name (default: muxmon)")

    # Split on '--' to separate launcher args from monitor-passthrough args
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
        monitors = [name for name, cls in sorted(muxmon.REGISTRY.items())
                     if cls.is_available()]
    else:
        monitors = args.monitors

    if not monitors:
        parser.print_help()
        sys.exit(1)

    # Resolve aliases and validate
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
    monitors = resolved

    launch(monitors, args.session, extra_args)


if __name__ == "__main__":
    main()
