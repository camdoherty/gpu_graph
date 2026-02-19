"""BaseMonitor — shared rendering engine for all terminal monitors.

Handles: argparse, deque management, draw loop with rate-limiting,
SIGWINCH resize, ANSI cursor-home double-buffering, unit auto-scaling,
plotext theming, and deadline-based tick loop.

Subclasses implement: name, default_title, add_args(), setup(), sample().
"""

from __future__ import annotations

import math
import signal
import sys
import time
import argparse
from abc import ABC, abstractmethod
from argparse import ArgumentParser, Namespace
from collections import deque
from dataclasses import dataclass, field

import plotext as plt

# ---- unit scaling ----

RATE_UNITS = [("B/s", 1), ("KB/s", 1024), ("MB/s", 1024**2), ("GB/s", 1024**3)]
SIZE_UNITS = [("B", 1), ("KB", 1024), ("MB", 1024**2), ("GB", 1024**3)]


def pick_unit(max_val: float, units: list[tuple[str, int]] | None = None) -> tuple[str, int]:
    """Choose the best unit so the peak value is readable."""
    if units is None:
        units = RATE_UNITS
    for name, divisor in reversed(units):
        if max_val >= divisor:
            return name, divisor
    return units[0]


def format_rate(bps: float, units: list[tuple[str, int]] | None = None) -> str:
    """Format a value into a human-readable string with auto-scaled units."""
    if units is None:
        units = RATE_UNITS
    for name, divisor in reversed(units):
        if bps >= divisor:
            return f"{bps / divisor:.1f} {name}"
    return f"{bps:.0f} {units[0][0]}"


# ---- series definition ----

@dataclass
class Series:
    """One data series on the chart."""
    name: str
    color: str
    label_fmt: str          # e.g. "↓ {}" or "{}"
    unit_mode: str          # "percent", "rate", or "fixed"
    data: deque = field(default=None, repr=False)
    current: float = 0.0

    def formatted_label(self) -> str:
        if self.unit_mode == "percent":
            return self.label_fmt.format(f"{self.current:.0f}%")
        elif self.unit_mode == "rate":
            return self.label_fmt.format(format_rate(self.current))
        else:
            return self.label_fmt.format(f"{self.current:.1f}")


# ---- base monitor ----

class BaseMonitor(ABC):
    """Abstract base for all terminal monitors.

    Lifecycle:
        1. __init__() parses args, calls setup()
        2. run() enters the blocking main loop
        3. sample() is called each tick
        4. cleanup() is called on exit
    """

    name: str = ""                # e.g. "cpu" — used by registry & launcher
    default_title: str = ""       # e.g. "CPU" — centered on chart

    def __init__(self, argv: list[str] | None = None):
        parser = ArgumentParser(description=f"{self.default_title} terminal monitor")
        # Universal flags
        parser.add_argument("--interval", type=float, default=0.5,
                            help="Update interval in seconds (default: 0.5)")
        parser.add_argument("--window", type=float, default=60.0,
                            help="Rolling history window in seconds (default: 60)")
        parser.add_argument("--title", default=None,
                            help="Override chart title")
        parser.add_argument("--no-legend", action="store_true",
                            help="Hide the legend labels")
        parser.add_argument(
            "--frame",
            action=argparse.BooleanOptionalAction,
            default=False,
            help="Show chart frame border (default: off)",
        )
        # Subclass-specific flags
        self.add_args(parser)
        self.args = parser.parse_args(argv)

        self.interval_s = max(0.1, self.args.interval)
        self.window_seconds = max(self.interval_s * 4, self.args.window)
        self.max_points = max(2, int(self.window_seconds / self.interval_s))
        self.xs = [i * self.interval_s - self.window_seconds for i in range(self.max_points)]
        self.title = self.args.title or self.default_title

        self._series: list[Series] = []
        self._series_map: dict[str, Series] = {}
        self._last_draw = 0.0

        self.setup(self.args)

    # ---- subclass interface ----

    def add_args(self, parser: ArgumentParser) -> None:
        """Override to add monitor-specific CLI flags."""

    @abstractmethod
    def setup(self, args: Namespace) -> None:
        """Called once after arg parsing. Use add_series() here."""

    @abstractmethod
    def sample(self) -> dict[str, float]:
        """Called each tick. Return {series_name: value}."""

    def cleanup(self) -> None:
        """Called on exit. Override to release resources."""

    def title_suffix(self) -> str:
        """Override to add dynamic info to the title (e.g. 'CPU 47%')."""
        return ""

    # ---- series management ----

    def add_series(self, name: str, *, color: str,
                   label_fmt: str | None = None,
                   unit_mode: str = "percent") -> None:
        """Register a data series. Call in setup()."""
        if label_fmt is None:
            label_fmt = name.upper() + " {}"
        s = Series(
            name=name, color=color, label_fmt=label_fmt,
            unit_mode=unit_mode,
            data=deque([0.0] * self.max_points, maxlen=self.max_points),
        )
        self._series.append(s)
        self._series_map[name] = s

    # ---- rendering ----

    def _draw(self) -> None:
        now = time.monotonic()
        if now - self._last_draw < 0.05:
            return
        self._last_draw = now

        plt.clf()
        plt.theme("clear")
        plt.plotsize(None, None)

        # Group series by unit_mode for scaling
        y_min, y_max = 0.0, 100.0
        all_percent = all(s.unit_mode == "percent" for s in self._series)
        all_rate = all(s.unit_mode == "rate" for s in self._series)
        unit_label = ""

        if all_percent:
            y_max = 100.0
            for s in self._series:
                label = s.formatted_label() if not self.args.no_legend else ""
                plt.plot(self.xs, list(s.data), label=label, color=s.color, marker="braille")

        elif all_rate:
            peak = max((max(s.data) for s in self._series), default=1.0)
            peak = max(peak, 1.0)
            unit_label, divisor = pick_unit(peak)
            for s in self._series:
                scaled = [v / divisor for v in s.data]
                label = s.formatted_label() if not self.args.no_legend else ""
                plt.plot(self.xs, scaled, label=label, color=s.color, marker="braille")
            all_scaled = [v / divisor for s in self._series for v in s.data]
            y_max = math.ceil(max(max(all_scaled), 0.01) * 1.15)

        else:
            # Mixed modes — scale each independently, use percent axis
            for s in self._series:
                if s.unit_mode == "rate":
                    peak = max(max(s.data), 1.0)
                    _, divisor = pick_unit(peak)
                    scaled = [v / divisor for v in s.data]
                    label = s.formatted_label() if not self.args.no_legend else ""
                    plt.plot(self.xs, scaled, label=label, color=s.color, marker="braille")
                else:
                    label = s.formatted_label() if not self.args.no_legend else ""
                    plt.plot(self.xs, list(s.data), label=label, color=s.color, marker="braille")

        plt.frame(self.args.frame)
        plt.xticks([])
        plt.yticks([])
        plt.ylim(y_min, y_max)
        plt.xlim(-self.window_seconds, 0)
        plt.grid(False, False)

        # Title
        suffix = self.title_suffix()
        title_parts = [self.title]
        if suffix:
            title_parts.append(suffix)
        if unit_label:
            title_parts.append(unit_label)
        title_text = "  ".join(title_parts)

        plt.text(title_text, x=-self.window_seconds / 2, y=y_max * 0.9,
                 color="default", alignment="center")

        sys.stdout.write("\033[H" + plt.build().rstrip() + "\033[J")
        sys.stdout.flush()

    # ---- main loop ----

    def run(self) -> None:
        """Blocking main loop. Ctrl+C to exit."""
        sys.stdout.write("\033[?25l")  # hide cursor
        sys.stdout.flush()

        def on_resize(signum, frame):
            self._draw()

        signal.signal(signal.SIGWINCH, on_resize)

        next_tick = time.monotonic()
        try:
            while True:
                next_tick += self.interval_s
                values = self.sample()
                for name, val in values.items():
                    s = self._series_map.get(name)
                    if s:
                        s.current = val
                        s.data.append(val)
                self._draw()
                time.sleep(max(0, next_tick - time.monotonic()))
        except KeyboardInterrupt:
            pass
        finally:
            self.cleanup()
            sys.stdout.write("\033[?25h")  # show cursor
            sys.stdout.flush()
            print("\nExiting...")

    # ---- availability check ----

    @classmethod
    def is_available(cls) -> bool:
        """Return True if this monitor can run on the current system."""
        return True
