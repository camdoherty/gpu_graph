#!/usr/bin/env python3
"""Minimal terminal network traffic monitor — download/upload rates."""
import time
import sys
import math
from collections import deque
import plotext as plt
import signal

# ---- config ----
INTERVAL_S = 0.5
WINDOW_SECONDS = 60
max_points = int(WINDOW_SECONDS / INTERVAL_S)

# X axis: fixed -60 → 0
xs = [i * INTERVAL_S - WINDOW_SECONDS for i in range(max_points)]
dl_rates = deque([0.0] * max_points, maxlen=max_points)
ul_rates = deque([0.0] * max_points, maxlen=max_points)

# ---- unit scaling ----
UNITS = [("B/s", 1), ("KB/s", 1024), ("MB/s", 1024**2), ("GB/s", 1024**3)]

def pick_unit(max_bps):
    """Choose the best unit so the peak value falls in a readable range."""
    for name, divisor in reversed(UNITS):
        if max_bps >= divisor:
            return name, divisor
    return UNITS[0]

# ---- network counters ----
def read_net_bytes():
    """Sum RX/TX bytes across all non-loopback interfaces from /proc/net/dev."""
    rx_total, tx_total = 0, 0
    with open("/proc/net/dev") as f:
        for line in f:
            if ":" not in line:
                continue
            iface, data = line.split(":", 1)
            if iface.strip() == "lo":
                continue
            parts = data.split()
            rx_total += int(parts[0])   # receive bytes
            tx_total += int(parts[8])   # transmit bytes
    return rx_total, tx_total

def format_rate(bps):
    """Format a bytes/sec value into a human-readable string."""
    _, divisor = pick_unit(bps)
    for name, div in reversed(UNITS):
        if bps >= div:
            return f"{bps / div:.1f} {name}"
    return f"{bps:.0f} B/s"

# ---- main loop ----
def main():
    sys.stdout.write("\033[?25l")  # hide cursor
    sys.stdout.flush()

    prev_rx, prev_tx = read_net_bytes()
    prev_time = time.monotonic()
    next_tick = prev_time + INTERVAL_S

    # Let first sample settle
    time.sleep(INTERVAL_S)

    # Shared state for redraw
    state = {
        "dl_rates": dl_rates,
        "ul_rates": ul_rates,
        "dl": 0.0,
        "ul": 0.0,
        "last_draw": 0.0
    }

    def draw():
        """Idempotent draw function"""
        # Rate limit redraws
        now = time.monotonic()
        if now - state["last_draw"] < 0.05:
            return
        state["last_draw"] = now

        # Compute scaling based on current window peak
        peak = max(max(state["dl_rates"]), max(state["ul_rates"]), 1.0)
        unit_name, divisor = pick_unit(peak)
        
        dl_scaled = [v / divisor for v in state["dl_rates"]]
        ul_scaled = [v / divisor for v in state["ul_rates"]]
        y_max = math.ceil(max(max(dl_scaled), max(ul_scaled), 0.01) * 1.15)

        dl_label = format_rate(state["dl"])
        ul_label = format_rate(state["ul"])

        plt.clf()
        plt.theme("clear")
        plt.plotsize(None, None)

        plt.plot(xs, dl_scaled, label=f"↓ {dl_label}", color="green", marker="braille")
        plt.plot(xs, ul_scaled, label=f"↑ {ul_label}", color="yellow", marker="braille")

        plt.frame(False)
        plt.xticks([])
        plt.yticks([])
        plt.ylim(0, y_max)
        plt.xlim(-WINDOW_SECONDS, 0)
        plt.grid(False, False)
        plt.text(f"Net  {unit_name}", x=-WINDOW_SECONDS / 2, y=y_max * 0.9, color="default", alignment="center")

        sys.stdout.write("\033[H" + plt.build().rstrip() + "\033[J")
        sys.stdout.flush()

    def on_resize(signum, frame):
        draw()

    signal.signal(signal.SIGWINCH, on_resize)

    try:
        while True:
            now = time.monotonic()
            dt = now - prev_time
            rx, tx = read_net_bytes()

            # Compute rates in bytes/sec
            dl = max(0.0, (rx - prev_rx) / dt)
            ul = max(0.0, (tx - prev_tx) / dt)
            prev_rx, prev_tx = rx, tx
            prev_time = now

            state["dl"], state["ul"] = dl, ul
            dl_rates.append(dl)
            ul_rates.append(ul)

            draw()

            time.sleep(max(0, next_tick - time.monotonic()))
            next_tick += INTERVAL_S

    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()
        print("\nExiting...")

if __name__ == "__main__":
    main()
