#!/usr/bin/env python3
"""Sequential, paired upload comparison of flush-only and audit2; synthetic DC segment only."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

from network_lab import Network, NAMES, memory_available

HERE = Path(__file__).resolve().parent


def paired_order(round_index):
    return ("flush", "auto") if round_index % 2 == 0 else ("auto", "flush")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--haproxy", default="haproxy")
    parser.add_argument("--wg", default="wg")
    parser.add_argument("--rtt", type=int, choices=(20, 50, 100, 200), default=100)
    parser.add_argument("--max-streams", type=int, choices=(1, 4, 8, 16, 32), default=4)
    parser.add_argument("--min-streams", type=int, choices=(1, 4, 8, 16, 32), default=1)
    parser.add_argument("--payload-mib", type=int, choices=(8, 16, 64, 256), default=16)
    parser.add_argument("--rate-mbps", type=int, choices=(100, 1000), default=100)
    parser.add_argument("--rounds", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--socket-details", action="store_true")
    args = parser.parse_args()
    if args.min_streams > args.max_streams:
        parser.error("minimum stream tier exceeds maximum")
    if args.payload_mib > 128 and args.max_streams < 4:
        parser.error("per-stream payload cannot exceed 128 MiB")
    root = Path(args.root).resolve()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    net = Network(SimpleNamespace(root=str(root), wg=args.wg, start_iperf=False, rtt_ms=args.rtt,
                                  loss_percent=0, rate_mbps=args.rate_mbps, mtu=1420))
    rows = []
    run_id = "ab-" + str(time.time_ns())
    destination = root / "results" / (run_id + ".json")
    manifest = {"classification": "SINGLE_VPS_SYNTHETIC", "date_utc": datetime.now(timezone.utc).isoformat(),
                "control": "3.5.5+tdh.audit1 flush-only; not unmodified 1.5.0",
                "candidate": "3.5.5+tdh.audit2; identical to 1.5.0 default core",
                "scope": "raw TCP mock DC segment; WG configured but payload bypasses WG; direct-to-Telemt upload",
                "rtt_ms_configured": args.rtt, "rate_mbps_per_direction": args.rate_mbps,
                "payload_bytes_total_per_trial": args.payload_mib * 1024**2, "direction": "U", "kernel": os.uname().release,
                "command": sys.argv, "rounds": args.rounds, "rows": rows, "complete": False}
    manifest["socket_observation"] = "detailed" if args.socket_details else "off"
    def save():
        destination.write_text(json.dumps(manifest, indent=2) + "\n")
    try:
        destination.parent.mkdir(mode=0o700, exist_ok=True)
        manifest["binary_sha256"] = {build: hashlib.sha256((root / ("telemt-" + build)).read_bytes()).hexdigest()
                                     for build in ("flush", "auto")}
        save()
        net.setup()
        with tempfile.TemporaryDirectory(prefix="tdh-audit-ab-") as folder:
            shared = Path(folder)
            os.chown(shared, 65534, 65534)
            status = shared / "dc.json"
            dc = subprocess.Popen(["ip", "netns", "exec", NAMES[2], sys.executable,
                                   str(HERE / "mock_dc_worker.py"), "--output", str(status), "--socket-timeout", "25"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            net.processes.append(dc)
            try:
                deadline = time.monotonic() + 5
                while not status.exists():
                    if dc.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError("mock DC readiness failed")
                    time.sleep(.1)
                tiers = [streams for streams in (1, 4, 8, 16, 32) if args.min_streams <= streams <= args.max_streams]
                # Fixed total payload per tier and paired order; no concurrent competing A/B workers.
                for streams in tiers:
                    if args.payload_mib * 1024**2 // streams > 128 * 1024**2:
                        continue
                    for turn in range(args.rounds):
                        for build in paired_order(turn):
                            if memory_available() < 256 * 1024**2:
                                raise RuntimeError("host memory margin before next load")
                            name = f"{run_id}-{streams}-{turn}-{build}.json"
                            output = destination.parent / name
                            argv = ["ip", "netns", "exec", NAMES[0], sys.executable, str(HERE / "high_rtt_worker.py"),
                                    "--root", str(root), "--haproxy", args.haproxy, "--build", build,
                                    "--output", str(output), "--rounds", "1", "--streams", str(streams),
                                    "--bytes", str(args.payload_mib * 1024**2), "--direction", "U",
                                    "--slow-readers", "--dc-timeout", "25", "--rate-mbps", str(args.rate_mbps),
                                    "--small-probes", "--observe-network", "--socket-observation",
                                    "detailed" if args.socket_details else "off"]
                            log_name = name.removesuffix('.json') + '.log'
                            log_file = (destination.parent / log_name).open('xb')
                            worker = subprocess.Popen(argv, stdout=log_file, stderr=subprocess.STDOUT)
                            net.processes.append(worker)
                            try:
                                try:
                                    code = worker.wait(timeout=100)
                                except subprocess.TimeoutExpired:
                                    worker.terminate()
                                    worker.wait(timeout=10)
                                    code = 124
                            finally:
                                log_file.close()
                            row = {"pair_round": turn + 1, "streams": streams, "build": build,
                                   "exit": code, "result_file": name, "worker_log": log_name, "command": argv}
                            if output.exists():
                                row["trial"] = json.loads(output.read_text())
                            rows.append(row)
                            save()
                            if code:
                                raise RuntimeError("paired trial failed; retained; refusing higher load")
                manifest["complete"] = True
            finally:
                if status.exists():
                    manifest["mock_dc"] = json.loads(status.read_text())
                dc.terminate()
                dc.wait(timeout=5)
    except BaseException as exc:
        manifest["failure"] = type(exc).__name__
        raise
    finally:
        try:
            net.close()
        finally:
            save()
    print(json.dumps({"result": str(destination), "complete": manifest["complete"], "paired_trials": len(rows)}))


if __name__ == "__main__":
    main()
