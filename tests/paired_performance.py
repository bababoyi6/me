#!/usr/bin/env python3
"""Bounded paired release comparison inside an owned, loopback-only CI namespace."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from core_install import archive_payload
from stable_acceptance import PINS, run
from paired_analysis import analyze, schedule

PROFILES = {
    'baseline': {'pairs': 6, 'payload': 262144, 'rate': 12500000,
                 'qdisc': ['netem', 'delay', '25ms', 'rate', '100mbit', 'limit', '10000']},
    'sustained': {'pairs': 4, 'payload': 1048576, 'rate': 125000000, 'qdisc': None},
    'impaired': {'pairs': 4, 'payload': 262144, 'rate': 12500000,
                 'qdisc': ['netem', 'delay', '25ms', '5ms', 'loss', '0.1%', 'rate', '100mbit', 'limit', '10000']},
}


def command(args):
    return subprocess.check_output(args, text=True, timeout=10).strip()


def resources():
    relative = next(s.split(':', 2)[2] for s in Path('/proc/self/cgroup').read_text().splitlines() if s.startswith('0::'))
    group = Path('/sys/fs/cgroup')/relative.lstrip('/')
    if not group.name.startswith('tdh-paired-'):
        raise RuntimeError('owned bounded transient unit required')
    def values(name):
        return {k: int(v) for k, v in (line.split() for line in (group/name).read_text().splitlines())}
    return {'memory_current': int((group/'memory.current').read_text()),
            'memory_peak': int((group/'memory.peak').read_text()), 'memory_events': values('memory.events'),
            'cpu': values('cpu.stat'), 'cpu_max': (group/'cpu.max').read_text().strip(),
            'memory_max': (group/'memory.max').read_text().strip(),
            'swap_max': (group/'memory.swap.max').read_text().strip(),
            'pids_current': int((group/'pids.current').read_text())}


def guard():
    if os.geteuid() != 0 or os.readlink('/proc/self/ns/net') == os.readlink('/proc/1/ns/net'):
        raise RuntimeError('root in a private network namespace required')
    links = json.loads(command(['ip', '-j', 'link', 'show']))
    if [r['ifname'] for r in links] != ['lo'] or command(['ip', 'route', 'show', 'default']):
        raise RuntimeError('strict loopback namespace required; no public traffic permitted')
    data = resources()
    if data['cpu_max'] != '200000 100000' or data['memory_max'] != str(1536*1024**2) or data['swap_max'] != '0':
        raise RuntimeError('expected 2 CPU total / 1.5GiB / zero swap budget')
    cpus = sorted(os.sched_getaffinity(0))
    if len(cpus) < 2:
        raise RuntimeError('two available CPUs required to separate core from load generator')
    return cpus[:2]


def write(path, value):
    temp = path.with_suffix('.partial')
    with temp.open('w') as target:
        json.dump(value, target, indent=2, allow_nan=False)
        target.write('\n'); target.flush()
        # The runner uploads this fixed-name, synthetic-metrics-only file as an
        # unprivileged user. Private binary/config staging remains under 0077.
        os.fchmod(target.fileno(), 0o644)
        os.fsync(target.fileno())
    temp.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stable-archive', required=True)
    parser.add_argument('--balanced2-archive', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--replica', required=True, type=int, choices=(1, 2))
    args = parser.parse_args()
    os.umask(0o077)
    output = Path(args.output)
    backend_cpu, controller_cpu = guard()
    source_commit = os.environ.get('TDH_SOURCE_COMMIT', '')
    if not re.fullmatch('[a-f0-9]{40}', source_commit):
        raise RuntimeError('immutable CI source commit required')
    original_affinity = os.sched_getaffinity(0)
    # Both binaries start with one available CPU, so their runtime worker count
    # is derived from the same limit. Then move ALL backend threads to CPU A;
    # clients, mock DC and HAProxy stay on CPU B. No host affinity is changed.
    os.sched_setaffinity(0, {controller_cpu})
    report = {'schema': 1, 'complete': False, 'accepted': False, 'replica': args.replica,
              'scope': 'same-job paired native FakeTLS/HAProxy/MTProto; synthetic DC; no public throughput/client/censorship claim',
              'source_commit': source_commit, 'os': platform.freedesktop_os_release()['PRETTY_NAME'],
              'kernel': os.uname().release, 'image_version': os.environ.get('ImageVersion'),
              'cpu_model': next((s.split(':',1)[1].strip() for s in Path('/proc/cpuinfo').read_text().splitlines() if s.startswith('model name')), None),
              'backend_cpu': backend_cpu, 'controller_cpu': controller_cpu,
              'backend_cpu_limit': 'all backend threads pinned to one logical CPU',
              'haproxy': command(['haproxy', '-v']).splitlines()[0],
              'manager_sha256': hashlib.sha256((Path(__file__).resolve().parents[1]/'telemt-dual-hop.sh').read_bytes()).hexdigest(),
              'resources_before': resources(), 'profiles': []}
    started = time.monotonic()
    write(output, report)
    try:
        with tempfile.TemporaryDirectory(prefix='tdh-paired-payloads-') as temp:
            binaries = {}
            for kind, (archive_sha, binary_sha, version) in PINS.items():
                content, _ = archive_payload(getattr(args, kind+'_archive'), archive_sha,
                                              version.removeprefix('telemt '), binary_sha, False)
                binary = Path(temp)/kind
                binary.write_bytes(content); binary.chmod(0o755)
                binaries[kind] = binary
            report['pins'] = PINS
            if args.replica == 1:
                # Isolate the previously asymmetric fixture timeout without
                # changing versions, resources, configuration or network.
                settings = PROFILES['baseline']
                command(['tc', 'qdisc', 'add', 'dev', 'lo', 'root', *settings['qdisc']])
                report['timeout_diagnostic'] = []
                for seconds in (8, 25):
                    result = run(binaries['balanced2'], 'balanced2', (100,), offline_lab=True,
                                 backend_cpu=backend_cpu, dc_timeout_seconds=seconds)
                    report['timeout_diagnostic'].append(result)
                    write(output, report)
            for name, settings in PROFILES.items():
                subprocess.run(['tc', 'qdisc', 'del', 'dev', 'lo', 'root'], capture_output=True, timeout=10)
                if settings['qdisc']:
                    command(['tc', 'qdisc', 'add', 'dev', 'lo', 'root', *settings['qdisc']])
                profile = {'name': name, 'expected_pairs': settings['pairs'], 'payload_bytes': settings['payload'],
                           'per_stream_rate_bytes_sec': settings['rate'], 'pairs': [],
                           'qdisc': json.loads(command(['tc', '-j', 'qdisc', 'show', 'dev', 'lo']))}
                report['profiles'].append(profile)
                for i, order in enumerate(schedule(settings['pairs'], args.replica), 1):
                    pair = {'pair': i, 'order': order, 'runs': {}}
                    profile['pairs'].append(pair)
                    for kind in order:
                        result = run(binaries[kind], kind, offline_lab=True, payload_bytes=settings['payload'],
                                     transfer_rate=settings['rate'], backend_cpu=backend_cpu)
                        pair['runs'][kind] = result
                        write(output, report)
                        print(json.dumps({'profile': name, 'pair': i, 'kind': kind, 'accepted': result['accepted']}), flush=True)
                        if not result['backend_cpu_affinity_verified']:
                            raise RuntimeError('backend CPU affinity drift')
                        # Fixed idle interval and fresh processes for both orders.
                        time.sleep(1)
                profile['analysis'] = analyze(profile)
            report['complete'] = True
    except BaseException as error:
        report['error_type'] = type(error).__name__
        raise
    finally:
        report['resources_after'] = resources()
        events = ('oom', 'oom_kill')
        report['oom_delta'] = {k: report['resources_after']['memory_events'][k]-report['resources_before']['memory_events'][k] for k in events}
        report['elapsed_seconds'] = time.monotonic()-started
        report['accepted'] = (report['complete'] and not any(report['oom_delta'].values()) and
                              all(p['analysis']['transport_accepted'] for p in report['profiles']))
        write(output, report)
        os.sched_setaffinity(0, original_affinity)
    return 0 if report['accepted'] else 1


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    raise SystemExit(main())
