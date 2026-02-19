"""Host-wide network traffic monitor — reads /proc/net/dev."""

from __future__ import annotations

import time
from argparse import ArgumentParser, Namespace

from muxmon import register
from muxmon.base import BaseMonitor


@register
class NetMonitor(BaseMonitor):
    name = "net"
    default_title = "Net"

    def add_args(self, parser: ArgumentParser) -> None:
        parser.add_argument("--interface", default=None,
                            help="Monitor a single NIC (e.g. enp0s31f6)")
        parser.add_argument("--exclude", default="",
                            help="Comma-separated NICs to skip (e.g. virbr0,tailscale0)")

    def setup(self, args: Namespace) -> None:
        self._interface = args.interface
        self._excludes = set(x.strip() for x in args.exclude.split(",") if x.strip())

        self.add_series("dl", color="green", label_fmt="↓ {}", unit_mode="rate")
        self.add_series("ul", color="yellow", label_fmt="↑ {}", unit_mode="rate")

        self._prev_rx, self._prev_tx = self._read_bytes()
        self._prev_time = time.monotonic()

    def sample(self) -> dict[str, float]:
        now = time.monotonic()
        rx, tx = self._read_bytes()
        dt = max(1e-6, now - self._prev_time)

        dl = max(0.0, (rx - self._prev_rx) / dt)
        ul = max(0.0, (tx - self._prev_tx) / dt)

        self._prev_rx, self._prev_tx = rx, tx
        self._prev_time = now
        return {"dl": dl, "ul": ul}

    def title_suffix(self) -> str:
        if self._interface:
            return self._interface
        return ""

    def _read_bytes(self) -> tuple[int, int]:
        """Sum RX/TX bytes across matching interfaces from /proc/net/dev."""
        rx_total, tx_total = 0, 0
        with open("/proc/net/dev") as f:
            for line in f:
                if ":" not in line:
                    continue
                iface, data = line.split(":", 1)
                iface = iface.strip()
                if iface == "lo":
                    continue
                if self._interface and iface != self._interface:
                    continue
                if iface in self._excludes:
                    continue
                parts = data.split()
                rx_total += int(parts[0])   # receive bytes
                tx_total += int(parts[8])   # transmit bytes
        return rx_total, tx_total


if __name__ == "__main__":
    NetMonitor().run()
