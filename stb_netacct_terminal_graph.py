#!/usr/bin/env python3
"""Terminal graph for StB external RX/TX using netacct JSON counters."""
import argparse
import json
import math
import signal
import sys
import time
from collections import deque
from pathlib import Path

import plotext as plt

# ---- defaults ----
INTERVAL_S = 0.5
WINDOW_SECONDS = 60.0
COUNTERS_FILE = "/run/stb-netacct/counters.json"
RX_KEY = "rx_bytes_total"
TX_KEY = "tx_bytes_total"

UNITS = [("B/s", 1), ("KB/s", 1024), ("MB/s", 1024**2), ("GB/s", 1024**3)]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Graph StB external throughput from netacct counters JSON."
    )
    parser.add_argument(
        "--counters-file",
        default=COUNTERS_FILE,
        help=f"Path to counters JSON (default: {COUNTERS_FILE})",
    )
    parser.add_argument(
        "--rx-key",
        default=RX_KEY,
        help=f"JSON key for RX total bytes (default: {RX_KEY})",
    )
    parser.add_argument(
        "--tx-key",
        default=TX_KEY,
        help=f"JSON key for TX total bytes (default: {TX_KEY})",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=INTERVAL_S,
        help=f"Update interval seconds (default: {INTERVAL_S})",
    )
    parser.add_argument(
        "--window",
        type=float,
        default=WINDOW_SECONDS,
        help=f"History window seconds (default: {WINDOW_SECONDS})",
    )
    return parser.parse_args()


def pick_unit(max_bps):
    for name, divisor in reversed(UNITS):
        if max_bps >= divisor:
            return name, divisor
    return UNITS[0]


def format_rate(bps):
    for name, div in reversed(UNITS):
        if bps >= div:
            return f"{bps / div:.1f} {name}"
    return f"{bps:.0f} B/s"


def read_stb_counters(path, rx_key, tx_key):
    """
    Returns ((rx_total, tx_total), status_tag).
    status_tag: ok | waiting | parse_err
    """
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, "waiting"
    except OSError:
        return None, "waiting"

    try:
        payload = json.loads(raw)
        rx = int(payload[rx_key])
        tx = int(payload[tx_key])
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None, "parse_err"

    if rx < 0 or tx < 0:
        return None, "parse_err"
    return (rx, tx), "ok"


def main():
    args = parse_args()

    interval_s = max(0.1, float(args.interval))
    window_seconds = max(interval_s * 4, float(args.window))
    max_points = max(2, int(window_seconds / interval_s))

    xs = [i * interval_s - window_seconds for i in range(max_points)]
    dl_rates = deque([0.0] * max_points, maxlen=max_points)
    ul_rates = deque([0.0] * max_points, maxlen=max_points)

    sys.stdout.write("\033[?25l")
    sys.stdout.flush()

    prev_totals = None
    prev_time = time.monotonic()
    next_tick = prev_time + interval_s

    state = {
        "dl_rates": dl_rates,
        "ul_rates": ul_rates,
        "dl": 0.0,
        "ul": 0.0,
        "status": "waiting",
        "last_draw": 0.0,
    }

    def draw():
        now = time.monotonic()
        if now - state["last_draw"] < 0.05:
            return
        state["last_draw"] = now

        peak = max(max(state["dl_rates"]), max(state["ul_rates"]), 1.0)
        unit_name, divisor = pick_unit(peak)

        dl_scaled = [v / divisor for v in state["dl_rates"]]
        ul_scaled = [v / divisor for v in state["ul_rates"]]
        y_max = math.ceil(max(max(dl_scaled), max(ul_scaled), 0.01) * 1.15)

        dl_label = format_rate(state["dl"])
        ul_label = format_rate(state["ul"])
        status_text = state["status"]

        plt.clf()
        plt.theme("clear")
        plt.plotsize(None, None)

        plt.plot(xs, dl_scaled, label=f"↓ {dl_label}", color="green", marker="braille")
        plt.plot(xs, ul_scaled, label=f"↑ {ul_label}", color="yellow", marker="braille")

        plt.frame(False)
        plt.xticks([])
        plt.yticks([])
        plt.ylim(0, y_max)
        plt.xlim(-window_seconds, 0)
        plt.grid(False, False)
        plt.text(
            f"StB Ext Net  {unit_name}  {status_text}",
            x=-window_seconds / 2,
            y=y_max * 0.9,
            color="default",
            alignment="center",
        )

        sys.stdout.write("\033[H" + plt.build().rstrip() + "\033[J")
        sys.stdout.flush()

    def on_resize(signum, frame):
        draw()

    signal.signal(signal.SIGWINCH, on_resize)

    try:
        while True:
            now = time.monotonic()
            dt = max(1e-6, now - prev_time)
            totals, status = read_stb_counters(args.counters_file, args.rx_key, args.tx_key)
            state["status"] = status

            if totals is None:
                dl = 0.0
                ul = 0.0
            elif prev_totals is None:
                dl = 0.0
                ul = 0.0
                prev_totals = totals
            else:
                rx, tx = totals
                prev_rx, prev_tx = prev_totals
                dl = max(0.0, (rx - prev_rx) / dt)
                ul = max(0.0, (tx - prev_tx) / dt)
                prev_totals = totals

            prev_time = now
            state["dl"] = dl
            state["ul"] = ul
            dl_rates.append(dl)
            ul_rates.append(ul)

            draw()

            time.sleep(max(0, next_tick - time.monotonic()))
            next_tick += interval_s

    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()
        print("\nExiting...")


if __name__ == "__main__":
    main()
