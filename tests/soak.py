#!/usr/bin/env python3
"""Five-minute, 20 Mbps aggregate loopback HAProxy integrity/reload soak.

Only synthetic streaming endpoints. Never connects to Telegram or a real proxy.
"""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import signal
import subprocess
import time

from lab import Lab


def main(args):
    def available():
        return next(int(r.split()[1]) * 1024 for r in Path("/proc/meminfo").read_text().splitlines() if r.startswith("MemAvailable:"))
    if available() < 192 * 1024**2:
        raise SystemExit("Insufficient memory safety margin")
    lab, snapshots = Lab(args.haproxy, master=True), []
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    try:
        started = time.monotonic()
        futures = [executor.submit(lab.transfer, "apple.com", direction, 375000000, 1250000) for direction in ("D", "U")]
        reloads, probes, last_probe = 0, 0, -10
        while not all(f.done() for f in futures):
            elapsed = time.monotonic() - started
            if elapsed > 335:
                raise RuntimeError("Soak deadline exceeded")
            if elapsed >= 150 and not reloads:
                lab.process.send_signal(signal.SIGUSR2)
                reloads += 1
            if elapsed - last_probe >= 10:
                assert lab.transfer("gs.apple.com", "D", 65536, 0)[0] == "2"
                probes += 1
                last_probe = elapsed
                memory = available()
                snapshots.append({"elapsed_seconds": elapsed, "memory_available_bytes": memory})
                if memory < 192 * 1024**2:
                    raise RuntimeError("Memory safety margin reached")
            time.sleep(.2)
        results = [f.result(timeout=1) for f in futures]
        assert all(result[0] == "1" for result in results)
        output = {"scope": "loopback synthetic HAProxy only; no WireGuard/Telemt/Telegram production claim",
                  "kernel": os.uname().release, "haproxy": subprocess.check_output([args.haproxy, "-v"], text=True).splitlines()[0],
                  "elapsed_seconds": time.monotonic() - started, "payload_bytes": 750000000,
                  "rate_mbps_aggregate": 20, "verified_long_streams": 2,
                  "verified_short_probes": probes, "master_worker_reloads_during_transfer": reloads,
                  "sha256_all": True, "memory_snapshots": snapshots}
        Path(args.output).write_text(json.dumps(output, indent=2) + "\n")
        print(json.dumps({k: v for k, v in output.items() if k != "memory_snapshots"}), flush=True)
    finally:
        lab.close()
        executor.shutdown(wait=True, cancel_futures=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--haproxy", default="haproxy")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    main(args)
