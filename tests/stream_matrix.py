#!/usr/bin/env python3
"""Disposable-host file cross-check; fixed 50 Mbps cap, 3 rounds, SHA256 verified.

Use only on an unused host. Creates the same private namespaces as network_lab.
No public listener, host network change, Telemt session or Telegram file is used.
"""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from types import SimpleNamespace

from network_lab import Network, NAMES, command, memory_available


def run(args):
    root = Path(args.root).resolve()
    rtt, loss = {"rtt100": (100, 0), "rtt200": (200, 0), "lossy200": (200, .5)}[args.profile]
    config = SimpleNamespace(root=str(root), rtt_ms=rtt, loss_percent=loss, rate_mbps=100,
                             mtu=1420, wg=args.wg, iperf=args.iperf)
    probe = str(Path(__file__).with_name("stream_probe.py"))
    lab, rows = Network(config), []
    try:
        lab.setup()
        process = subprocess.Popen(["ip", "netns", "exec", NAMES[2], "python3", probe, "--serve"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        lab.processes.append(process)
        time.sleep(.3)
        for turn in range(3):
            for streams in (1, 4):
                for direction in ("D", "U"):
                    for mode in (("raw", "wireguard") if turn % 2 == 0 else ("wireguard", "raw")):
                        if memory_available() < 192 * 1024 * 1024:
                            raise RuntimeError("memory safety margin")
                        peer = "198.51.100.2" if mode == "raw" else "10.77.1.2"
                        size, rate = 18750000 // streams, 6250000 // streams
                        def transfer(_):
                            return json.loads(command("ip", "netns", "exec", NAMES[0], "python3", probe,
                                                      "--peer", peer, "--direction", direction,
                                                      "--size", str(size), "--rate", str(rate), timeout=40))
                        started = time.monotonic()
                        with concurrent.futures.ThreadPoolExecutor(max_workers=streams) as pool:
                            outcomes = list(pool.map(transfer, range(streams)))
                        elapsed = time.monotonic() - started
                        rows.append({"round": turn + 1, "mode": mode, "streams": streams, "direction": direction,
                                     "receiver_mbps": size * streams * 8 / elapsed / 1e6,
                                     "elapsed_seconds": elapsed,
                                     "sha256_verified": all(o["sha256_verified"] for o in outcomes),
                                     "available_memory_after_bytes": memory_available()})
                        result = {"scope": "private namespace synthetic file transfer; not Telegram or production",
                                  "kernel": os.uname().release, "rtt_ms_configured": rtt,
                                  "loss_percent_per_direction": loss, "link_rate_mbps": 100,
                                  "application_limit_mbps": 50, "wireguard_mtu": 1420, "rows": rows}
                        (root / "results" / ("stream-" + args.profile + ".json")).write_text(json.dumps(result, indent=2) + "\n")
                        time.sleep(.5)
        print(json.dumps({"profile": args.profile, "completed": len(rows),
                          "sha256_all": all(r["sha256_verified"] for r in rows)}), flush=True)
    finally:
        lab.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--iperf", default="iperf3")
    parser.add_argument("--wg", default="wg")
    parser.add_argument("--profile", choices=("rtt100", "rtt200", "lossy200"), required=True)
    arguments = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    run(arguments)
