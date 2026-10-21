#!/usr/bin/env python3
"""Explicit disposable-host probe of the five fixed health-check TCP targets.

No MTProto handshake, account, login or file throughput measurement is performed.
"""
import argparse
import json
import math
from pathlib import Path
import socket
import statistics
import time

ADDRESSES = ("149.154.175.50", "149.154.167.51", "149.154.175.100", "149.154.167.91", "149.154.171.5")


def main(root):
    root = Path(root).resolve()
    if not root.name.startswith("tdh-audit-") or not root.is_dir() or root.stat().st_mode & 0o077:
        raise SystemExit("Use an existing private (0700) tdh-audit-* lab directory")
    rows, deadline = [], time.monotonic() + 70
    for turn in range(5):
        for index, address in enumerate(ADDRESSES, 1):
            if time.monotonic() > deadline:
                raise SystemExit("Global probe budget reached")
            started, ok = time.monotonic(), False
            try:
                with socket.create_connection((address, 443), timeout=2):
                    ok = True
            except OSError:
                pass
            rows.append({"round": turn + 1, "dc_index": index, "ok": ok,
                         "connect_ms": (time.monotonic() - started) * 1000 if ok else None})
            time.sleep(.1)
    summary = []
    for index in range(1, 6):
        values = sorted(r["connect_ms"] for r in rows if r["dc_index"] == index and r["ok"])
        summary.append({"dc_index": index, "successes": len(values),
                        "median_ms": statistics.median(values) if values else None,
                        "p95_ms": values[math.ceil(.95 * len(values)) - 1] if values else None,
                        "min_ms": min(values) if values else None, "max_ms": max(values) if values else None})
    result = {"scope": "temporary host to configured health-check targets TCP/443 setup only; no MTProto/files or production path",
              "rows": rows, "summary": summary}
    (root / "results").mkdir(exist_ok=True)
    (root / "results/dc-connect.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    main(args.root)
