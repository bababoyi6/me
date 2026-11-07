#!/usr/bin/env python3
"""Bounded real Fake TLS/MTProto synthetic transfers; no real Telegram account/files.

Runs both exact release payloads under identical conditions. Invoke in a private
network namespace for tc/netem; the tool itself never changes host networking.
"""
import argparse
import concurrent.futures
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import tarfile
import tempfile
import time

from e2e_lab import FullLab

PINS = {
    'balanced2': ('b4fec3efd10288a9f211ad958337fa314a15486e72fd5c50ab870f22a19737e5',
                  '5dc5d9b5ac43a0f12a402159e127b3b69e18ccb355c99b0322e73d870d907100',
                  'telemt 3.5.5+tdh.balanced2'),
    'stable': ('e6f46b2f3becf29bfd555cec8a059e429f7c082c1af402499b3a7940cf8ebd34',
               '1954f18faeb9f5838e9444880f2ffb00704c6bd5b2836bc86dd64611537d49b8', 'telemt 3.5.16'),
}


def stats(pid):
    status = Path(f'/proc/{pid}/status').read_text().splitlines()
    fields = Path(f'/proc/{pid}/stat').read_text().split()
    return {'cpu_seconds': (int(fields[13])+int(fields[14]))/os.sysconf('SC_CLK_TCK'),
            'rss_bytes': next(int(line.split()[1])*1024 for line in status if line.startswith('VmRSS:')),
            'fds': len(list(Path(f'/proc/{pid}/fd').iterdir()))}


def summary(values):
    ordered = sorted(values)
    return {'p50': statistics.median(ordered), 'p95': ordered[math.ceil(.95*len(ordered))-1]} if ordered else None


def run(binary, kind, cohorts=(30, 50, 100), *, offline_lab=False, payload_bytes=262144, transfer_rate=12500000,
        backend_cpu=None, dc_timeout_seconds=25):
    if payload_bytes not in (262144, 1048576) or transfer_rate not in (12500000, 125000000):
        raise ValueError('bounded acceptance workload required')
    lab = FullLab(binary, experimental_binary=(PINS[kind][1], PINS[kind][2]) if kind == 'balanced2' else None,
                  offline_lab=offline_lab)
    # Queueing in the 100-stream shaped test can exceed eight seconds. Both
    # protocol endpoints must use the same budget, not just the client socket.
    if dc_timeout_seconds not in (8, 25):
        lab.close()
        raise ValueError('bounded mock DC diagnostic timeout required')
    lab.dc.socket_timeout = dc_timeout_seconds
    if backend_cpu is not None:
        if not isinstance(backend_cpu, int) or backend_cpu < 0:
            lab.close()
            raise ValueError('explicit backend CPU required')
        try:
            for task in Path(f'/proc/{lab.telemt.pid}/task').iterdir():
                try:
                    os.sched_setaffinity(int(task.name), {backend_cpu})
                except ProcessLookupError:
                    pass
        except BaseException:
            lab.close()
            raise
    rows, total = [], 0
    result = None
    try:
        # Persistent tail acknowledgements must complete without an EOF rescue.
        for domain in ('apple.com', 'gs.apple.com'):
            for direction in ('U', 'D'):
                lab.transfer(domain, direction, 4096, 12500000, persistent=True, io_timeout=25)
                total += 1
        baseline = stats(lab.telemt.pid)
        for concurrency in cohorts:
            for direction in ('U', 'D'):
                before = stats(lab.telemt.pid)
                proxy_before = stats(lab.haproxy.pid)
                controller_before = time.process_time()
                started = time.monotonic()
                successes, errors, samples = [], [], []
                with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
                    futures = [pool.submit(lab.transfer, ('apple.com', 'gs.apple.com')[i % 2], direction,
                                           payload_bytes, transfer_rate, persistent=True, io_timeout=25)
                               for i in range(concurrency)]
                    while not all(future.done() for future in futures):
                        samples.append(stats(lab.telemt.pid))
                        time.sleep(.05)
                    for future in futures:
                        try:
                            successes.append(future.result())
                        except Exception as error:
                            errors.append({'type': type(error).__name__, 'phase': getattr(error, 'lab_phase', 'unknown')})
                elapsed = time.monotonic()-started
                after = stats(lab.telemt.pid)
                proxy_after = stats(lab.haproxy.pid)
                total += len(successes)
                rows.append({'concurrency': concurrency, 'direction': direction, 'successes': len(successes),
                             'errors': errors, 'success_rate': len(successes)/concurrency,
                             'aggregate_mbps': sum(row['bytes'] for row in successes)*8/elapsed/1e6,
                             'wall_seconds': elapsed, 'handshake_ms': summary([row['handshake_ms'] for row in successes]),
                             'completion_ms': summary([row['elapsed_seconds']*1000 for row in successes]),
                             'backend_cpu_one_core_percent': 100*(after['cpu_seconds']-before['cpu_seconds'])/elapsed,
                             'controller_and_mock_cpu_one_core_percent': 100*(time.process_time()-controller_before)/elapsed,
                             'haproxy_cpu_one_core_percent': 100*(proxy_after['cpu_seconds']-proxy_before['cpu_seconds'])/elapsed,
                             'backend_peak_rss_bytes': max([before['rss_bytes'], after['rss_bytes']]+[s['rss_bytes'] for s in samples]),
                             'backend_fds_before': before['fds'], 'backend_fds_after': after['fds']})
        time.sleep(2)
        after = stats(lab.telemt.pid)
        validation_error = None
        try:
            lab.validate(total)
        except Exception as error:
            validation_error = type(error).__name__
        policy = lab.telemt_config.read_text()
        policy = policy.replace(f'port = {lab.listen}\n', 'port = $NATIVE_PORT\n')
        policy = policy.replace(f'127.0.0.1:{lab.api}', '127.0.0.1:$API_PORT')
        policy = policy.replace(f'127.0.0.1:{lab.dc.server_address[1]}', '127.0.0.1:$DC_PORT')
        result = {'version': lab.version, 'rows': rows, 'before_load': baseline, 'after_idle': after,
                'config_policy_sha256': hashlib.sha256(policy.encode()).hexdigest(),
                'mock_dc_socket_timeout_seconds': lab.dc.socket_timeout, 'client_io_timeout_seconds': 25,
                'offline_bootstrap': offline_lab, 'payload_bytes': payload_bytes, 'transfer_rate': transfer_rate,
                'mock_dc_completed': lab.dc.completed, 'mock_dc_errors': lab.dc.errors,
                'validation_error': validation_error,
                'config_unchanged': hashlib.sha256(lab.telemt_config.read_bytes()).hexdigest() == lab.config_hash,
                'accepted': validation_error is None and all(not row['errors'] for row in rows)}
        if backend_cpu is not None:
            result['backend_cpu_affinity_verified'] = all(os.sched_getaffinity(int(t.name)) == {backend_cpu}
                for t in Path(f'/proc/{lab.telemt.pid}/task').iterdir())
        return result
    finally:
        lab.close()
        if result is not None:
            result['private_processes_closed'] = all(p.poll() is not None for p in lab.processes) and not lab.root.exists()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stable-archive', required=True)
    parser.add_argument('--balanced2-archive', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--conditions', required=True)
    parser.add_argument('--concurrency', type=int, action='append', choices=(30, 50, 100))
    parser.add_argument('--candidate-only', action='store_true')
    args = parser.parse_args()
    report = {'scope': 'real HAProxy/FakeTLS/MTProto and synthetic DC/payload only; shared Linux host',
              'conditions': args.conditions, 'kernel': os.uname().release, 'results': []}
    with tempfile.TemporaryDirectory(prefix='tdh-upgrade-load-') as temp:
        for kind in (('stable',) if args.candidate_only else ('balanced2', 'stable')):
            archive = Path(getattr(args, kind+'_archive'))
            if hashlib.sha256(archive.read_bytes()).hexdigest() != PINS[kind][0]:
                raise ValueError('archive checksum mismatch')
            with tarfile.open(archive, 'r:gz') as source:
                member = next(m for m in source if m.name == 'telemt' and m.isfile() and m.size < 64*1024**2)
                payload = source.extractfile(member).read()
            if hashlib.sha256(payload).hexdigest() != PINS[kind][1]:
                raise ValueError('binary checksum mismatch')
            binary = Path(temp)/'telemt'
            binary.write_bytes(payload)
            binary.chmod(0o755)
            report['results'].append(run(binary, kind, tuple(args.concurrency or (30, 50, 100))))
            Path(args.output).write_text(json.dumps(report, indent=2)+'\n')
    report['accepted'] = all(row['accepted'] for row in report['results'])
    Path(args.output).write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({'accepted': report['accepted'], 'versions': [r['version'] for r in report['results']]}))
    return 0 if report['accepted'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
