"""Disk I/O throughput monitor — reads /proc/diskstats sector deltas."""

from __future__ import annotations

import os
from argparse import ArgumentParser, Namespace

from muxmon import register
from muxmon.base import BaseMonitor

import time


SECTOR_SIZE = 512  # always 512 in /proc/diskstats


@register
class StorageMonitor(BaseMonitor):
    name = "storage"
    default_title = "Disk I/O"

    def add_args(self, parser: ArgumentParser) -> None:
        parser.add_argument("--device", default=None,
                            help="Block device name, e.g. nvme0n1, sda (default: auto-detect root)")
        parser.add_argument("--all-devices", action="store_true",
                            help="Sum I/O across all physical devices")
        parser.add_argument("--show-iops", action="store_true",
                            help="Show IOPS instead of throughput")

    def setup(self, args: Namespace) -> None:
        self._all_devices = args.all_devices
        self._show_iops = args.show_iops

        self.add_series("read", color="green", label_fmt="R {}", unit_mode="rate")
        self.add_series("write", color="yellow", label_fmt="W {}", unit_mode="rate")

        if self._all_devices:
            self._device = None
        else:
            self._device = args.device or self._detect_root_device()

        self._prev = self._read_diskstats()
        self._prev_time = time.monotonic()

    def sample(self) -> dict[str, float]:
        now = time.monotonic()
        cur = self._read_diskstats()
        dt = max(1e-6, now - self._prev_time)

        d_read = cur["sectors_read"] - self._prev["sectors_read"]
        d_write = cur["sectors_written"] - self._prev["sectors_written"]

        read_bps = max(0.0, d_read * SECTOR_SIZE / dt)
        write_bps = max(0.0, d_write * SECTOR_SIZE / dt)

        self._prev = cur
        self._prev_time = now
        return {"read": read_bps, "write": write_bps}

    def title_suffix(self) -> str:
        if self._device:
            return self._device
        return ""

    def _read_diskstats(self) -> dict[str, int]:
        """Read /proc/diskstats, return aggregate or per-device counters.

        Fields (0-indexed after major minor name):
          [3] reads_completed  [5] sectors_read
          [6] writes_completed [9] sectors_written
        """
        sectors_read = 0
        sectors_written = 0
        with open("/proc/diskstats") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 14:
                    continue
                devname = parts[2]
                if self._device:
                    if devname != self._device:
                        continue
                    sectors_read = int(parts[5])
                    sectors_written = int(parts[9])
                    break
                elif self._all_devices:
                    # Skip partitions: only whole devices (no trailing digit,
                    # or device-mapper names like dm-N)
                    if devname.startswith("loop"):
                        continue
                    sectors_read += int(parts[5])
                    sectors_written += int(parts[9])
        return {"sectors_read": sectors_read, "sectors_written": sectors_written}

    @staticmethod
    def _detect_root_device() -> str:
        """Detect the block device backing / via stat major:minor."""
        st = os.stat("/")
        target_major = os.major(st.st_dev)
        target_minor = os.minor(st.st_dev)
        with open("/proc/diskstats") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3:
                    continue
                if int(parts[0]) == target_major and int(parts[1]) == target_minor:
                    return parts[2]
        raise RuntimeError(
            f"Could not detect root block device (major={target_major}, minor={target_minor}). "
            "Use --device to specify one manually."
        )


if __name__ == "__main__":
    StorageMonitor().run()
