#!/usr/bin/env python3
"""Export STB external RX/TX byte counters to JSON for graphing."""
from __future__ import annotations

import argparse
import json
import os
import pwd
import grp
import re
import subprocess
import sys
import time
from pathlib import Path

TX_COMMENT = "STB_EXT_TX"
RX_COMMENT = "STB_EXT_RX"

COUNTER_RE = re.compile(r"^\[(\d+):(\d+)\]\s+-A\s+(\S+)\s+(.*)$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read iptables/ip6tables counters and export JSON totals."
    )
    parser.add_argument(
        "--output",
        default=os.environ.get("COUNTERS_FILE", "/run/stb-netacct/counters.json"),
        help="JSON output path",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=float(os.environ.get("INTERVAL", "0.5")),
        help="Polling interval in seconds",
    )
    parser.add_argument(
        "--out-chain",
        default=os.environ.get("CHAIN_OUT", "STB_EXT_OUT"),
        help="Mangle chain used for TX counting",
    )
    parser.add_argument(
        "--in-chain",
        default=os.environ.get("CHAIN_IN", "STB_EXT_IN"),
        help="Mangle chain used for RX counting",
    )
    parser.add_argument(
        "--owner",
        default=os.environ.get("STB_OWNER", "cad"),
        help="Owner username for output file",
    )
    parser.add_argument(
        "--group",
        default=os.environ.get("STB_GROUP", "cad"),
        help="Group name for output file",
    )
    return parser.parse_args()


def run_save(binary: str) -> str:
    return subprocess.check_output(
        [binary, "-t", "mangle", "-c"],
        text=True,
        stderr=subprocess.DEVNULL,
    )


def bytes_for_comment(save_text: str, chain: str, comment: str) -> int:
    total = 0
    for line in save_text.splitlines():
        match = COUNTER_RE.match(line)
        if not match:
            continue
        line_chain = match.group(3)
        if line_chain != chain:
            continue
        body = match.group(4)
        if (f'--comment "{comment}"' not in body) and (f"--comment {comment}" not in body):
            continue
        total += int(match.group(2))
    return total


def read_totals(out_chain: str, in_chain: str) -> tuple[int, int]:
    v4 = run_save("iptables-save")
    v6 = run_save("ip6tables-save")

    tx = bytes_for_comment(v4, out_chain, TX_COMMENT) + bytes_for_comment(v6, out_chain, TX_COMMENT)
    rx = bytes_for_comment(v4, in_chain, RX_COMMENT) + bytes_for_comment(v6, in_chain, RX_COMMENT)
    return rx, tx


def resolve_ids(owner: str, group: str) -> tuple[int, int]:
    uid = pwd.getpwnam(owner).pw_uid
    gid = grp.getgrnam(group).gr_gid
    return uid, gid


def write_payload(path: Path, payload: dict, uid: int, gid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    os.chown(tmp, uid, gid)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def main() -> int:
    args = parse_args()
    interval = max(0.1, float(args.interval))
    out_path = Path(args.output)

    try:
        uid, gid = resolve_ids(args.owner, args.group)
    except KeyError as exc:
        print(f"owner/group lookup failed: {exc}", file=sys.stderr)
        return 2

    rx_total = 0
    tx_total = 0
    status = "init"
    next_tick = time.monotonic()

    while True:
        try:
            rx_total, tx_total = read_totals(args.out_chain, args.in_chain)
            status = "ok"
        except Exception as exc:  # pragma: no cover
            status = f"read_error:{type(exc).__name__}"
            print(f"counter read failed: {exc}", file=sys.stderr)

        payload = {
            "rx_bytes_total": rx_total,
            "tx_bytes_total": tx_total,
            "ts_monotonic": time.monotonic(),
            "ts_unix": time.time(),
            "status": status,
        }
        try:
            write_payload(out_path, payload, uid, gid)
        except Exception as exc:  # pragma: no cover
            print(f"write failed: {exc}", file=sys.stderr)

        next_tick += interval
        time.sleep(max(0.0, next_tick - time.monotonic()))


if __name__ == "__main__":
    raise SystemExit(main())
