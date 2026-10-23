#!/usr/bin/env python3
"""Explicit bounded loopback Telemt version / fast_mode comparison, synthetic only.

Maximum aggregate rate 200 Mbps, 1-3 rounds, 1-3 seconds per trial. Does not run
from the manager, alter installed services, or measure Telegram file throughput.
"""
import argparse
import concurrent.futures
import json
import math
import os
from pathlib import Path
import signal
import statistics
import time

from e2e_lab import FullLab


def cpu_seconds(process):
    fields = Path(f"/proc/{process.pid}/stat").read_text().rsplit(")", 1)[1].split()
    return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")


def run(args):
    rows = []
    variants = [(binary, fast) for binary in (args.baseline, args.candidate) for fast in (True, False)]
    for turn in range(args.rounds):
        for binary, fast in variants[::1 if turn % 2 == 0 else -1]:
            lab = FullLab(binary, args.haproxy, fast=fast)
            try:
                for streams in (1, 4):
                    for direction in ("D", "U"):
                        rate = int(args.rate_mbps * 1000000 / 8 / streams)
                        size = int(rate * args.seconds)
                        telemt_before, haproxy_before = cpu_seconds(lab.telemt), cpu_seconds(lab.haproxy)
                        started = time.monotonic()
                        with concurrent.futures.ThreadPoolExecutor(max_workers=streams) as pool:
                            results = list(pool.map(lambda _: lab.transfer("apple.com", direction, size, rate), range(streams)))
                        elapsed = time.monotonic() - started
                        gib = size * streams / 1024**3
                        rows.append({"round": turn + 1, "telemt": lab.version, "fast_mode": fast,
                                     "streams": streams, "direction": direction, "bytes": size * streams,
                                     "elapsed_seconds": elapsed, "mbps": size * streams * 8 / elapsed / 1000000,
                                     "telemt_cpu_seconds_per_gib": (cpu_seconds(lab.telemt) - telemt_before) / gib,
                                     "haproxy_cpu_seconds_per_gib": (cpu_seconds(lab.haproxy) - haproxy_before) / gib,
                                     "sha256_verified": all(result["sha256_verified"] for result in results)})
                # Each variant runs 1+1+4+4 transfers.
                lab.validate(10)
                print(json.dumps({"round_completed": turn + 1, "telemt": lab.version, "fast_mode": fast}), flush=True)
            finally:
                lab.close()
    groups = []
    for version in dict.fromkeys(row["telemt"] for row in rows):
        for fast in (True, False):
            for streams in (1, 4):
                for direction in ("D", "U"):
                    selected = [row for row in rows if (row["telemt"], row["fast_mode"], row["streams"], row["direction"]) ==
                                (version, fast, streams, direction)]
                    values = sorted(row["mbps"] for row in selected)
                    groups.append({"telemt": version, "fast_mode": fast, "streams": streams, "direction": direction,
                                   "n": len(values), "median_mbps": statistics.median(values),
                                   "p95_mbps": values[math.ceil(len(values) * .95) - 1],
                                   "min_mbps": values[0], "max_mbps": values[-1],
                                   "median_telemt_cpu_seconds_per_gib": statistics.median(row["telemt_cpu_seconds_per_gib"] for row in selected)})
    output = {"scope": "application-capped synthetic encrypted loopback; no WG, network emulation or Telegram files",
              "kernel": os.uname().release, "logical_cpus": os.cpu_count(), "rate_cap_mbps": args.rate_mbps,
              "seconds_per_trial": args.seconds, "rounds": args.rounds,
              "cpu_scope": "process user+system time only; excludes kernel workers and synthetic client/DC",
              "rows": rows, "summary": groups}
    Path(args.output).write_text(json.dumps(output, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--haproxy", default="haproxy")
    parser.add_argument("--output", required=True)
    parser.add_argument("--rate-mbps", type=int, choices=range(10, 201), default=200)
    parser.add_argument("--seconds", type=int, choices=range(1, 4), default=3)
    parser.add_argument("--rounds", type=int, choices=range(1, 4), default=3)
    options = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    run(options)
