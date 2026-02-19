#!/usr/bin/env python3
"""Terminal graph for StB service traffic using socket byte counters from ss."""
import argparse
import ipaddress
import math
import os
import re
import signal
import subprocess
import sys
import time
from collections import defaultdict, deque

import plotext as plt

# ---- config ----
INTERVAL_S = 0.5
WINDOW_SECONDS = 60
SERVICE_REFRESH_S = 2.0

DEFAULT_SERVICES = [
    "stb-next-host-agent@split.service",
    "stb-next-server@split.service",
    "stb-next-shell@split.service",
]

UNITS = [("B/s", 1), ("KB/s", 1024), ("MB/s", 1024**2), ("GB/s", 1024**3)]
PID_RE = re.compile(r"pid=(\d+)")
BYTES_SENT_RE = re.compile(r"bytes_sent:(\d+)")
BYTES_RECV_RE = re.compile(r"bytes_received:(\d+)")
CGNAT = ipaddress.ip_network("100.64.0.0/10")


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Graph StB service network throughput by polling TCP socket counters "
            "for service root processes and all descendants."
        )
    )
    parser.add_argument(
        "--services",
        nargs="+",
        default=DEFAULT_SERVICES,
        help="Systemd user service names (space/comma separated).",
    )
    parser.add_argument("--interval", type=float, default=INTERVAL_S, help="Refresh interval in seconds.")
    parser.add_argument("--window", type=float, default=WINDOW_SECONDS, help="History window in seconds.")
    parser.add_argument(
        "--include-internal",
        action="store_true",
        help="Include private/loopback/link-local peers (default: external peers only).",
    )
    return parser.parse_args()


def parse_services(raw_services):
    services = []
    for item in raw_services:
        for part in item.split(","):
            name = part.strip()
            if name:
                services.append(name)
    return services


def pick_unit(max_bps):
    for name, divisor in reversed(UNITS):
        if max_bps >= divisor:
            return name, divisor
    return UNITS[0]


def format_rate(bps):
    for name, divisor in reversed(UNITS):
        if bps >= divisor:
            return f"{bps / divisor:.1f} {name}"
    return f"{bps:.0f} B/s"


def get_service_main_pids(services):
    pids = set()
    for service in services:
        result = subprocess.run(
            ["systemctl", "--user", "show", "-p", "MainPID", "--value", service],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            continue
        value = result.stdout.strip()
        if value.isdigit():
            pid = int(value)
            if pid > 0:
                pids.add(pid)
    return pids


def get_children_map():
    children = defaultdict(set)
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            with open(f"/proc/{pid}/stat") as f:
                raw = f.read()
        except OSError:
            continue

        right_paren = raw.rfind(")")
        if right_paren == -1:
            continue
        rest = raw[right_paren + 2 :].split()
        if len(rest) < 2:
            continue
        try:
            ppid = int(rest[1])
        except ValueError:
            continue
        children[ppid].add(pid)
    return children


def get_descendants(root_pids, children_map):
    seen = set()
    stack = list(root_pids)
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        stack.extend(children_map.get(pid, ()))
    return seen


def extract_host(endpoint):
    """Convert endpoint like '1.2.3.4:443' or '[::1]:1234' to plain host."""
    endpoint = endpoint.strip()
    if not endpoint:
        return ""
    if endpoint.startswith("["):
        idx = endpoint.rfind("]")
        if idx == -1:
            return endpoint
        host = endpoint[1:idx]
    elif endpoint.count(":") == 1:
        host = endpoint.rsplit(":", 1)[0]
    else:
        host = endpoint
    return host.split("%", 1)[0]


def is_internal_endpoint(endpoint):
    host = extract_host(endpoint)
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return True

    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped

    if isinstance(addr, ipaddress.IPv4Address) and addr in CGNAT:
        return True

    return (
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def read_socket_totals(target_pids, include_internal):
    """
    Return cumulative socket counters for tracked PIDs.
    key: (pid, local_endpoint, remote_endpoint)
    value: (bytes_sent, bytes_received)
    """
    if not target_pids:
        return {}

    result = subprocess.run(
        ["ss", "-Htnpi"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return {}

    totals = {}
    current = None
    for line in result.stdout.splitlines():
        if not line:
            continue

        if line[0].isspace():
            if current is None:
                continue
            sent_match = BYTES_SENT_RE.search(line)
            recv_match = BYTES_RECV_RE.search(line)
            if not sent_match or not recv_match:
                continue

            sent = int(sent_match.group(1))
            recv = int(recv_match.group(1))

            if not include_internal and is_internal_endpoint(current["remote"]):
                continue

            for pid in current["tracked_pids"]:
                key = (pid, current["local"], current["remote"])
                totals[key] = (sent, recv)
            continue

        parts = line.split()
        if len(parts) < 5:
            current = None
            continue
        pids = {int(pid) for pid in PID_RE.findall(line)}
        tracked = pids & target_pids
        if not tracked:
            current = None
            continue

        current = {
            "local": parts[3],
            "remote": parts[4],
            "tracked_pids": tracked,
        }
    return totals


def compute_deltas(cur_totals, prev_totals):
    """Return (download_bytes, upload_bytes) since previous sample."""
    dl_bytes = 0
    ul_bytes = 0
    for key, (sent, recv) in cur_totals.items():
        prev_sent, prev_recv = prev_totals.get(key, (sent, recv))
        ul_bytes += max(0, sent - prev_sent)
        dl_bytes += max(0, recv - prev_recv)
    return dl_bytes, ul_bytes


def main():
    args = parse_args()
    services = parse_services(args.services)
    interval_s = max(0.1, args.interval)
    window_seconds = max(interval_s * 4, args.window)

    max_points = max(2, int(window_seconds / interval_s))
    xs = [i * interval_s - window_seconds for i in range(max_points)]
    dl_rates = deque([0.0] * max_points, maxlen=max_points)
    ul_rates = deque([0.0] * max_points, maxlen=max_points)

    sys.stdout.write("\033[?25l")
    sys.stdout.flush()

    state = {
        "dl_rates": dl_rates,
        "ul_rates": ul_rates,
        "dl": 0.0,
        "ul": 0.0,
        "tracked_pids": 0,
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
        mode = "Ext" if not args.include_internal else "All"

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
            f"StB {mode} TCP  {unit_name}  pids:{state['tracked_pids']}",
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

    next_tick = time.monotonic()
    last_pid_refresh = 0.0
    tracked_pids = set()
    prev_totals = {}
    prev_sample_time = None

    try:
        while True:
            now = time.monotonic()
            if now - last_pid_refresh >= SERVICE_REFRESH_S:
                root_pids = get_service_main_pids(services)
                children_map = get_children_map()
                tracked_pids = get_descendants(root_pids, children_map)
                state["tracked_pids"] = len(tracked_pids)
                last_pid_refresh = now

            cur_totals = read_socket_totals(tracked_pids, args.include_internal)
            if prev_sample_time is None:
                dl = 0.0
                ul = 0.0
            else:
                dt = max(1e-6, now - prev_sample_time)
                dl_bytes, ul_bytes = compute_deltas(cur_totals, prev_totals)
                dl = dl_bytes / dt
                ul = ul_bytes / dt

            prev_totals = cur_totals
            prev_sample_time = now

            state["dl"] = dl
            state["ul"] = ul
            dl_rates.append(dl)
            ul_rates.append(ul)

            draw()

            next_tick += interval_s
            time.sleep(max(0, next_tick - time.monotonic()))

    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()
        print("\nExiting...")


if __name__ == "__main__":
    main()
