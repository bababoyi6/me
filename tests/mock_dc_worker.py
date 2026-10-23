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
args = parser.parse_args()
if subprocess.check_output(["ip", "netns", "identify"], text=True, timeout=3).strip() != "tdh-audit-b":
    parser.error("mock DC must run in its private namespace")
signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
if os.geteuid() == 0:
    os.setgroups([])
    os.setgid(65534)
    os.setuid(65534)
dc = MockDc("198.51.100.2", 26000, socket_timeout=args.socket_timeout)
def listen_counters():
    lines = Path('/proc/net/netstat').read_text().splitlines()
    for index, line in enumerate(lines[:-1]):
        if line.startswith('TcpExt:'):
            values = dict(zip(line.split()[1:], map(int, lines[index+1].split()[1:])))
            return {key: values.get(key) for key in ('ListenOverflows', 'ListenDrops', 'TCPSynRetrans')}
    return {}
try:
    while True:
        Path(args.output).write_text(json.dumps({"completed": dc.completed, "errors": dc.errors,
                                                "socket_timeout_seconds": args.socket_timeout,
                                                "connect_only": dc.connect_only_checks,
                                                "tcp_listen_counters": listen_counters()}) + "\n")
        time.sleep(.1)
finally:
    dc.shutdown()
    dc.server_close()
