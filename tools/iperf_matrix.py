#!/usr/bin/env python3
"""Explicit bounded TCP tests to fixed TDH tunnel peers; never starts a server."""
import argparse
import json
import math
import shutil
import statistics
import subprocess
import time


def summarize(values):
    values = sorted(values)
    return {"median": statistics.median(values), "p95": values[math.ceil(.95 * len(values)) - 1],
            "min": min(values), "max": max(values)}


def run(args):
    if not shutil.which("iperf3"):
        raise SystemExit("缺少 iperf3；本工具不会安装软件或启动服务。")
    source = "10.77.1.1" if args.peer == "10.77.1.2" else "10.77.2.1"
    rows = []
    for turn in range(args.rounds):
        for streams in (1, 4):
            for reverse in (False, True):
                # iperf3's -b is PER STREAM. Divide the aggregate budget.
                bitrate = args.rate_mbps * 1000000 // streams
                command = ["iperf3", "-4", "-c", args.peer, "-B", source, "-p", "5201",
                           "-J", "-t", str(args.seconds), "-P", str(streams), "-b", str(bitrate),
                           "--connect-timeout", "3000"] + (["-R"] if reverse else [])
                row = {"round": turn + 1, "streams": streams, "direction": "download" if reverse else "upload"}
                try:
                    result = subprocess.run(command, capture_output=True, text=True, timeout=args.seconds + 10)
                    payload = json.loads(result.stdout)
                    if result.returncode or "error" in payload:
                        raise ValueError("iperf failed")
                    end = payload["end"]
                    row.update({"ok": True, "receiver_mbps": end["sum_received"]["bits_per_second"] / 1000000,
                                "sender_retransmits": end["sum_sent"].get("retransmits"),
                                "seconds": end["sum_received"]["seconds"]})
                except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
                    # Do not copy raw errors, addresses or server strings into reports.
                    row.update({"ok": False, "error": "测试失败/超时；检查隧道、临时服务及访问控制。"})
                rows.append(row)
                time.sleep(args.cooldown)
    summary = []
    for streams in (1, 4):
        for direction in ("upload", "download"):
            selected = [r for r in rows if r["streams"] == streams and r["direction"] == direction and r["ok"]]
            summary.append({"streams": streams, "direction": direction, "successful_rounds": len(selected),
                            "receiver_mbps": summarize([r["receiver_mbps"] for r in selected]) if selected else None})
    print(json.dumps({"说明": "仅入口到一个后端的 WireGuard TCP 测试，不代表 Telegram 真实速度；到达限速值只能说明容量下界。",
                      "schema": 1, "rate_limit_mbps_aggregate": args.rate_mbps,
                      "rows": rows, "summary": summary}, ensure_ascii=False, indent=2))
    return 0 if all(r["ok"] for r in rows) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--peer", choices=("10.77.1.2", "10.77.2.2"), required=True)
    parser.add_argument("--rate-mbps", type=int, choices=range(1, 101), default=20)
    parser.add_argument("--seconds", type=int, choices=range(3, 16), default=8)
    parser.add_argument("--rounds", type=int, choices=range(3, 11), default=5)
    parser.add_argument("--cooldown", type=int, choices=range(2, 11), default=3)
    raise SystemExit(run(parser.parse_args()))
