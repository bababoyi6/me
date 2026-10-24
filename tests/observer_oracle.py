#!/usr/bin/env python3
"""Cross-check lab netlink counters against ss/tc and socket options, at rest."""
import argparse
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from types import SimpleNamespace

from kernel_observer import observe_sockets, qdiscs_in_namespace
from network_lab import Network, NAMES


def socket_check():
    with socket.socket() as listener, socket.socket() as client:
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        client.connect(('127.0.0.1', port))
        with listener.accept()[0] as accepted:
            client.sendall(b'bounded-observer-oracle')
            assert accepted.recv(64) == b'bounded-observer-oracle'
            started = time.monotonic()
            rows = observe_sockets(os.getpid(), port)
            duration = time.monotonic() - started
            assert len(rows) == 1 and rows[0]['role'] == 'client'
            row = rows[0]
            assert row['rb'] == accepted.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
            assert row['tb'] == accepted.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF)
            native = subprocess.check_output(['ss', '-Htinmp', 'state', 'established',
                                              f'( sport = :{port} )'], text=True, timeout=3)
            assert f'pid={os.getpid()},' in native
            assert int(re.search(r'\brb(\d+)', native)[1]) == row['rb']
            assert int(re.search(r'\btb(\d+)', native)[1]) == row['tb']
            assert row['bytes_received'] == len(b'bounded-observer-oracle')
            return {'socket_options_and_ss_match': True, 'netlink_seconds': duration, 'sample': row}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root')
    parser.add_argument('--wg', default='wg')
    parser.add_argument('--socket-child', action='store_true')
    args = parser.parse_args()
    if args.socket_child:
        print(json.dumps(socket_check()))
        return
    if not args.root:
        parser.error('--root required')
    net = Network(SimpleNamespace(root=args.root, wg=args.wg, start_iperf=False, rtt_ms=100,
                                  loss_percent=0, rate_mbps=100, mtu=1420))
    report = {'classification': 'SINGLE_VPS_OBSERVER_ORACLE', 'qdisc': [], 'complete': False}
    original = os.stat('/proc/self/ns/net').st_ino
    try:
        net.setup()
        for name in NAMES:
            def native():
                return { (row['dev'], row['kind']): row for row in json.loads(subprocess.check_output(
                    ['ip', 'netns', 'exec', name, 'tc', '-j', '-s', 'qdisc', 'show'], text=True, timeout=3))
                    if row['kind'] in ('fq', 'netem') }
            before = native()
            started = time.monotonic()
            rows = qdiscs_in_namespace(name)
            duration = time.monotonic() - started
            after = native()
            assert os.stat('/proc/self/ns/net').st_ino == original
            assert {(row['dev'], row['kind']) for row in rows} == set(before) == set(after)
            stable = 0
            for row in rows:
                key = row['dev'], row['kind']
                for field in ('bytes', 'packets', 'drops', 'overlimits', 'requeues', 'backlog', 'qlen'):
                    if field in before[key] and field in after[key] and before[key][field] == after[key][field]:
                        assert row.get(field) == before[key][field], (name, field)
                        stable += 1
            assert stable >= len(rows) * 6
            report['qdisc'].append({'namespace': name, 'stable_fields_matched': stable,
                                    'netlink_seconds': duration, 'rows': rows})
        report['socket'] = json.loads(subprocess.check_output(
            ['ip', 'netns', 'exec', NAMES[0], sys.executable, __file__, '--socket-child'], text=True, timeout=10))
        report['complete'] = True
    finally:
        net.close()
        report['original_namespace_restored'] = os.stat('/proc/self/ns/net').st_ino == original
        destination = Path(args.root) / 'results' / 'observer-oracle.json'
        destination.parent.mkdir(exist_ok=True)
        destination.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
