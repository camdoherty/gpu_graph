#!/usr/bin/env python3
"""Minimal terminal GPU monitor — utilization & memory via NVML."""
import time
import sys
import os
from collections import deque
import plotext as plt

# ---- config ----
INTERVAL_S = 0.5
WINDOW_SECONDS = 60
GPU_INDEX = 0

# ---- data storage ----
max_points = int(WINDOW_SECONDS / INTERVAL_S)
xs = [i * INTERVAL_S - WINDOW_SECONDS for i in range(max_points)]
gpu_util = deque([0.0] * max_points, maxlen=max_points)
mem_util = deque([0.0] * max_points, maxlen=max_points)

# ---- GPU query ----
MOCK_MODE = os.environ.get("MOCK_MODE") == "1"

if not MOCK_MODE:
    import pynvml
    pynvml.nvmlInit()
    _handle = pynvml.nvmlDeviceGetHandleByIndex(GPU_INDEX)

def get_gpu_metrics():
    if MOCK_MODE:
        import random
        return random.uniform(20, 80), random.uniform(10, 50)
    rates = pynvml.nvmlDeviceGetUtilizationRates(_handle)
    mem_info = pynvml.nvmlDeviceGetMemoryInfo(_handle)
    mem_pct = (mem_info.used / mem_info.total) * 100
    return float(rates.gpu), mem_pct

# ---- main loop ----
def main():
    sys.stdout.write("\033[?25l")  # hide cursor
    sys.stdout.flush()

    next_tick = time.monotonic()
    try:
        while True:
            next_tick += INTERVAL_S
            g, m = get_gpu_metrics()

            gpu_util.append(g)
            mem_util.append(m)

            plt.clf()
            plt.theme("clear")
            plt.plotsize(None, None)

            plt.plot(xs, list(gpu_util), label=f"GPU  {g:.0f}%", color="cyan", marker="braille")
            plt.plot(xs, list(mem_util), label=f"Mem  {m:.0f}%", color="magenta", marker="braille")

            plt.frame(False)
            plt.xticks([])
            plt.yticks([])
            plt.text(f"GPU {GPU_INDEX}", x=-WINDOW_SECONDS / 2, y=90, color="default", alignment="center")
            plt.ylim(0, 100)
            plt.xlim(-WINDOW_SECONDS, 0)
            plt.grid(False, False)

            sys.stdout.write("\033[H" + plt.build().rstrip() + "\033[J")
            sys.stdout.flush()

            time.sleep(max(0, next_tick - time.monotonic()))

    except KeyboardInterrupt:
        pass
    finally:
        if not MOCK_MODE:
            pynvml.nvmlShutdown()
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()
        print("\nExiting...")

if __name__ == "__main__":
    main()
