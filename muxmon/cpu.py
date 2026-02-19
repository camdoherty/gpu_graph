"""CPU utilization monitor — reads /proc/stat jiffie deltas."""

from __future__ import annotations

import os
from argparse import ArgumentParser, Namespace

from muxmon import register
from muxmon.base import BaseMonitor


@register
class CpuMonitor(BaseMonitor):
    name = "cpu"
    default_title = "CPU"

    _COLORS = ["cyan", "magenta", "green", "yellow", "red", "blue", "white", "orange"]

    def add_args(self, parser: ArgumentParser) -> None:
        parser.add_argument("--per-core", action="store_true",
                            help="One line per core instead of aggregate usr/sys")
        parser.add_argument("--show-iowait", action="store_true",
                            help="Add iowait as a third series")

    def setup(self, args: Namespace) -> None:
        self._per_core = args.per_core
        self._show_iowait = args.show_iowait

        if self._per_core:
            ncores = os.cpu_count() or 1
            for i in range(ncores):
                self.add_series(f"c{i}", color=self._COLORS[i % len(self._COLORS)],
                                label_fmt=f"C{i} {{}}", unit_mode="percent")
        else:
            self.add_series("usr", color="cyan", label_fmt="Usr {}", unit_mode="percent")
            self.add_series("sys", color="magenta", label_fmt="Sys {}", unit_mode="percent")
            if self._show_iowait:
                self.add_series("iow", color="red", label_fmt="IOw {}", unit_mode="percent")

        self._prev = self._read_jiffies()

    def sample(self) -> dict[str, float]:
        cur = self._read_jiffies()
        result = {}

        if self._per_core:
            ncores = os.cpu_count() or 1
            for i in range(ncores):
                key = f"cpu{i}"
                if key in cur and key in self._prev:
                    total_pct = self._compute_usage(self._prev[key], cur[key])
                    result[f"c{i}"] = total_pct
        else:
            if "cpu" in cur and "cpu" in self._prev:
                prev, now = self._prev["cpu"], cur["cpu"]
                delta = [n - p for n, p in zip(now, prev)]
                total = sum(delta) or 1
                # fields: user nice system idle iowait irq softirq steal
                usr = ((delta[0] + delta[1]) / total) * 100
                sys_ = ((delta[2] + delta[5] + delta[6]) / total) * 100
                result["usr"] = usr
                result["sys"] = sys_
                if self._show_iowait:
                    result["iow"] = (delta[4] / total) * 100

        self._prev = cur
        return result

    def title_suffix(self) -> str:
        if "cpu" in self._prev:
            # Show combined total usage in title
            total_series = [s for s in self._series]
            combined = sum(s.current for s in total_series)
            if self._per_core:
                ncores = os.cpu_count() or 1
                combined = combined / ncores if ncores > 0 else combined
            return f"{combined:.0f}%"
        return ""

    def _compute_usage(self, prev: list[int], cur: list[int]) -> float:
        delta = [n - p for n, p in zip(cur, prev)]
        total = sum(delta) or 1
        idle = delta[3] + delta[4]  # idle + iowait
        return ((total - idle) / total) * 100

    @staticmethod
    def _read_jiffies() -> dict[str, list[int]]:
        """Parse /proc/stat → {cpu_id: [user, nice, system, idle, iowait, ...]}"""
        result = {}
        with open("/proc/stat") as f:
            for line in f:
                if not line.startswith("cpu"):
                    continue
                parts = line.split()
                result[parts[0]] = [int(x) for x in parts[1:]]
        return result


if __name__ == "__main__":
    CpuMonitor().run()
