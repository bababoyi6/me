"""One bounded calibration: iperf userspace pacing versus the existing WG path.

No Telemt build, production change, host sysctl, or performance promotion.
Retain full iperf JSON and private TCP/queue observations, including failures.
"""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time
from types import SimpleNamespace

from network_lab import Network, NAMES, command
from paired_performance import resources, write


def observe():
    return {
        'elapsed_clock': time.monotonic(),
        'tcp': {ns: command('ip', 'netns', 'exec', ns, 'ss', '-tni', '-m',
                            '( sport = :5201 or dport = :5201 )')
                for ns in (NAMES[0], NAMES[2])},
        'router_queues': command('ip', 'netns', 'exec', NAMES[1], 'tc', '-s', '-j', 'qdisc', 'show'),
    }


def main(output):
    if os.geteuid() != 0 or os.environ.get('GITHUB_ACTIONS') != 'true':
        raise RuntimeError('disposable CI only')
    source = os.environ.get('TDH_SOURCE_COMMIT', '')
    if not re.fullmatch('[a-f0-9]{40}', source):
        raise RuntimeError('immutable source commit required')
    before = resources()
    if (before['cpu_max'] != '300000 100000' or
            before['memory_max'] != str(1536 * 1024**2) or before['swap_max'] != '0'):
        raise RuntimeError('registered resource limits required')
    cpus = sorted(os.sched_getaffinity(0))
    if len(cpus) < 3:
        raise RuntimeError('three available CPUs required')
    original_affinity = os.sched_getaffinity(0)
    # Match the prior controls exactly: both iperf endpoints share frontend CPU.
    os.sched_setaffinity(0, {cpus[1]})
    report = {'plan': 'capacity-pacer-v1', 'source_commit': source, 'complete': False,
              'scope': 'synthetic private namespaces only; no proxy throughput or promotion claim',
              'rtt_ms': 100, 'rate_mbps': 200, 'loss_percent': 0, 'mtu': 1420,
              'cpu_affinity': [cpus[1]], 'kernel': os.uname().release,
              'iperf_version': command('iperf3', '--version'), 'rows': [], 'resources_before': before,
              'host_socket_caps_read_only': command('sysctl', 'net.core.rmem_max', 'net.core.wmem_max')}
    net = None
    process = None
    write(output, report)
    try:
        with tempfile.TemporaryDirectory(prefix='tdh-audit-capacity-') as folder:
            net = Network(SimpleNamespace(root=folder, wg='wg', iperf='iperf3',
                rtt_ms=100, rate_mbps=200, loss_percent=0, mtu=1420))
            net.setup()
            report['socket_defaults'] = {ns: command('ip', 'netns', 'exec', ns, 'sysctl',
                'net.ipv4.tcp_rmem', 'net.ipv4.tcp_wmem', 'net.ipv4.tcp_congestion_control',
                'net.ipv4.tcp_moderate_rcvbuf')
                for ns in (NAMES[0], NAMES[2])}
            # Four pairs, balanced AB/BA separately within each direction.
            for direction, sequence in (('U', ('200M', '0', '0', '200M')),
                                        ('D', ('0', '200M', '200M', '0'))):
                for bitrate in sequence:
                    row = {'direction': direction, 'bitrate': bitrate, 'samples': []}
                    report['rows'].append(row)
                    argv = ['ip', 'netns', 'exec', NAMES[0], 'iperf3', '-c', '10.77.1.2',
                            '-t', '6', '-O', '2', '-b', bitrate, '-P', '1', '-J',
                            '--connect-timeout', '3000']
                    if direction == 'D':
                        argv.append('-R')
                    started = time.monotonic()
                    with tempfile.TemporaryFile(mode='w+') as capture:
                        process = subprocess.Popen(argv, stdout=capture, stderr=subprocess.STDOUT)
                        try:
                            while process.poll() is None:
                                if time.monotonic() - started > 18:
                                    raise RuntimeError('bounded capacity probe timed out')
                                row['samples'].append(observe())
                                time.sleep(1)
                            capture.seek(0)
                            row['raw_output'] = capture.read(1024 * 1024)
                            row['exit'] = process.returncode
                            row['iperf'] = json.loads(row['raw_output'])
                            if row['exit'] or row['iperf'].get('error'):
                                raise RuntimeError('private capacity probe failed; see retained row')
                            row['receiver_mbps'] = row['iperf']['end']['sum_received']['bits_per_second'] / 1e6
                        finally:
                            if process.poll() is None:
                                process.kill()
                            process.wait(timeout=3)
                            row['elapsed_seconds'] = time.monotonic() - started
                            write(output, report)
                    time.sleep(1)
            report['complete'] = True
    finally:
        report['cleanup_errors'] = []
        if process is not None and process.poll() is None:
            process.kill(); process.wait(timeout=3)
        if net is not None:
            try:
                net.close()
            except Exception as exc:
                report['cleanup_errors'].append(type(exc).__name__)
        os.sched_setaffinity(0, original_affinity)
        report['resources_after'] = resources()
        report['namespaces_removed'] = not set(NAMES).intersection(
            line.split()[0] for line in command('ip', 'netns', 'list').splitlines())
        report['private_processes_closed'] = net is None or all(p.poll() is not None for p in net.processes)
        report['oom_delta'] = {key: report['resources_after']['memory_events'][key] - before['memory_events'][key]
                               for key in ('oom', 'oom_kill')}
        report['accepted'] = (report['complete'] and report['namespaces_removed'] and
            report['private_processes_closed'] and not report['cleanup_errors'] and
            report['resources_after']['pids_current'] == 1 and not any(report['oom_delta'].values()))
        write(output, report)
    return 0 if report['accepted'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    raise SystemExit(main(parser.parse_args().output))
