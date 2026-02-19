"""Memory utilization monitor — reads /proc/meminfo."""

from __future__ import annotations

from argparse import ArgumentParser, Namespace

from muxmon import register
from muxmon.base import BaseMonitor, SIZE_UNITS, format_rate


@register
class MemoryMonitor(BaseMonitor):
    name = "memory"
    default_title = "Mem"

    def add_args(self, parser: ArgumentParser) -> None:
        parser.add_argument("--show-swap", action="store_true",
                            help="Add swap usage as a third series")
        parser.add_argument("--show-buffers", action="store_true",
                            help="Split cache into Buffers + Cached")

    def setup(self, args: Namespace) -> None:
        self._show_swap = args.show_swap
        self.add_series("used", color="cyan", label_fmt="Used {}", unit_mode="percent")
        self.add_series("cache", color="magenta", label_fmt="Cache {}", unit_mode="percent")
        if self._show_swap:
            self.add_series("swap", color="red", label_fmt="Swap {}", unit_mode="percent")

    def sample(self) -> dict[str, float]:
        info = self._read_meminfo()
        total = info.get("MemTotal", 1)
        avail = info.get("MemAvailable", total)
        cached = info.get("Cached", 0) + info.get("Buffers", 0)

        used_pct = ((total - avail) / total) * 100 if total else 0
        cache_pct = (cached / total) * 100 if total else 0

        result = {"used": used_pct, "cache": cache_pct}

        if self._show_swap:
            swap_total = info.get("SwapTotal", 0)
            swap_free = info.get("SwapFree", 0)
            if swap_total > 0:
                result["swap"] = ((swap_total - swap_free) / swap_total) * 100
            else:
                result["swap"] = 0.0

        self._total_kb = total
        self._avail_kb = avail
        return result

    def title_suffix(self) -> str:
        total = getattr(self, "_total_kb", 0)
        avail = getattr(self, "_avail_kb", 0)
        if total > 0:
            used_bytes = (total - avail) * 1024
            total_bytes = total * 1024
            return f"{format_rate(used_bytes, SIZE_UNITS)} / {format_rate(total_bytes, SIZE_UNITS)}"
        return ""

    @staticmethod
    def _read_meminfo() -> dict[str, int]:
        """Parse /proc/meminfo → {key: value_in_kB}"""
        result = {}
        with open("/proc/meminfo") as f:
            for line in f:
                if ":" not in line:
                    continue
                key, rest = line.split(":", 1)
                parts = rest.split()
                if parts:
                    try:
                        result[key] = int(parts[0])
                    except ValueError:
                        pass
        return result


if __name__ == "__main__":
    MemoryMonitor().run()
