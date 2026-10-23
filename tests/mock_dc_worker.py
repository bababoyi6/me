"""Synthetic DC role for the private high-RTT namespace experiment."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time

from e2e_lab import MockDc

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", required=True)
parser.add_argument("--socket-timeout", type=int, choices=(8, 25), default=8)
parser.add_argument("--topology", choices=("high-rtt", "dualhop"), default="high-rtt")
args = parser.parse_args()
expected = "tdh-audit-dc" if args.topology == "dualhop" else "tdh-audit-b"
if subprocess.check_output(["ip", "netns", "identify"], text=True, timeout=3).strip() != expected:
    parser.error("mock DC must run in its private namespace")
signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
if os.geteuid() == 0:
    os.setgroups([])
    os.setgid(65534)
    os.setuid(65534)
dcs = [MockDc(address, 26000, socket_timeout=args.socket_timeout) for address in
       (("198.18.0.1", "198.18.0.5") if args.topology == "dualhop" else ("198.51.100.2",))]
def listen_counters():
    lines = Path('/proc/net/netstat').read_text().splitlines()
    for index, line in enumerate(lines[:-1]):
        if line.startswith('TcpExt:'):
            values = dict(zip(line.split()[1:], map(int, lines[index+1].split()[1:])))
            return {key: values.get(key) for key in ('ListenOverflows', 'ListenDrops', 'TCPSynRetrans')}
    return {}
try:
    while True:
        records = [{"completed": dc.completed, "errors": dc.errors,
                                                "socket_timeout_seconds": args.socket_timeout,
                                                "connect_only": dc.connect_only_checks,
                                                "tcp_listen_counters": listen_counters()} for dc in dcs]
        destination = Path(args.output)
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps({"backends": records} if args.topology == "dualhop" else records[0]) + "\n")
        temporary.replace(destination)
        time.sleep(.1)
finally:
    for dc in dcs:
        dc.shutdown()
        dc.server_close()
