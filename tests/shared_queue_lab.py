"""Paired shared outer-link queue screen for the private dual-WireGuard lab."""
from concurrent.futures import ThreadPoolExecutor
import json
import re
import subprocess
import threading
import time
from types import SimpleNamespace

from e2e_lab import FullLab
from mixed_experience_analysis import mixed_result, quantiles
from mixed_workload import run_mixed
from network_lab import NAMES, command
from paired_performance import resources
from path_capacity import controls


SHARED_LINK = ((NAMES[0], 'tdh-la0'), (NAMES[1], 'tdh-la1'))
ORDER = ('pfifo', 'fq_codel', 'fq_codel', 'pfifo')
DOMAIN_A, DOMAIN_B = 'apple.com', 'gs.apple.com'
MIB = 1024 ** 2


def save(path, report):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)


def tc(namespace, *args):
    result = subprocess.run(('ip', 'netns', 'exec', namespace, 'tc', *args),
                            capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise RuntimeError(f"{namespace}: tc {' '.join(args)}: {result.stderr.strip()[:160]}")
    return result.stdout.strip()


def prepare_shared_path(network, results):
    """Place propagation at the two branches and rate at the common link."""
    for namespace, device in ((NAMES[1], 'tdh-lb0'), (NAMES[1], 'tdh-lc0'),
                              (NAMES[2], 'tdh-lb1'), ('tdh-audit-b2', 'tdh-lc1')):
        tc(namespace, 'qdisc', 'replace', 'dev', device, 'root', 'netem', 'delay', '50ms', 'limit', '10000')
    for namespace, device in SHARED_LINK:
        tc(namespace, 'qdisc', 'replace', 'dev', device, 'root', 'handle', '1:', 'htb', 'default', '10')
        tc(namespace, 'class', 'add', 'dev', device, 'parent', '1:', 'classid', '1:10',
           'htb', 'rate', '100mbit', 'ceil', '100mbit', 'quantum', '1514')
    set_policy('pfifo')
    calibration = []
    measurements = {}
    try:
        for index in (1, 2):
            raw = command('ip', 'netns', 'exec', NAMES[0], 'ping', '-n', '-c', '3', '-W', '2',
                          f'10.77.{index}.2', timeout=8)
            match = re.search(r'\bmin/avg/max/mdev = [0-9.]+/([0-9.]+)/', raw)
            if not match or not 95 <= float(match[1]) <= 125:
                raise RuntimeError('private tunnel RTT outside the preregistered range')
            measurements[index] = float(match[1])
        controls(network, 100, calibration)
        if any(not 60 <= row['mbps'] <= 105 for row in calibration):
            raise RuntimeError('receiver-local capacity outside the preregistered range')
    finally:
        save(results / 'dual-shared-calibration.json', {'link_mbps_each_direction': 100,
            'rtt_ms': 100, 'measured_idle_tunnel_rtt_ms': measurements,
            'controls': calibration, 'queue': snapshot('pfifo')})


def set_policy(policy):
    if policy not in ORDER:
        raise ValueError('unregistered shared queue')
    for namespace, device in SHARED_LINK:
        leaf = re.search(r'^qdisc (\w+) 10: parent 1:10\b',
                         tc(namespace, 'qdisc', 'show', 'dev', device), re.M)
        if leaf and leaf[1] not in ORDER:
            raise RuntimeError('unexpected shared queue on the private test class')
        if leaf:
            if leaf[1] == policy:
                continue
            # The same qdisc handle cannot change kind in place.
            tc(namespace, 'qdisc', 'del', 'dev', device, 'parent', '1:10', 'handle', '10:')
        tc(namespace, 'qdisc', 'add', 'dev', device, 'parent', '1:10', 'handle', '10:',
           policy, 'limit', '1000')


def snapshot(policy):
    # Jammy HTB can write formatted xstats into JSON output.
    observed = {}
    for namespace, device in SHARED_LINK:
        qdisc = tc(namespace, '-s', '-d', 'qdisc', 'show', 'dev', device)
        classes = tc(namespace, '-s', '-d', 'class', 'show', 'dev', device)
        if ('qdisc htb 1: root' not in qdisc or f'qdisc {policy} 10: parent 1:10' not in qdisc
                or 'class htb 1:10' not in classes or 'rate 100Mbit ceil 100Mbit' not in classes):
            raise RuntimeError('shared queue layout or rate did not match the plan')
        observed[device] = {'qdisc': qdisc, 'class': classes}
    return observed


def transfer(endpoint, domain, direction, size):
    return FullLab.transfer(endpoint, domain, direction, size, 125000000,
                            fragment=True, persistent=True, io_timeout=15)


def solo(endpoint, counts, domain):
    old = counts()
    rates = {}
    for direction in ('D', 'U'):
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(transfer, endpoint, domain, direction, 8 * MIB) for _ in range(2)]
            rows = [job.result(timeout=24) for job in jobs]
        rates[direction] = {'mbps': 16 * MIB * 8 / (time.monotonic() - started) / 1e6,
                            'transfers': rows}
    expected = [4, 0] if domain == DOMAIN_A else [0, 4]
    delta = [new - past for past, new in zip(old, counts())]
    if delta != expected:
        raise RuntimeError('single-line traffic used the wrong backend')
    return {'domain_index': 1 if domain == DOMAIN_A else 2, 'rates': rates, 'assignments': delta}


def failover(endpoint, shared, counts, wait_state):
    flag = shared / 'backend1.down'
    flag.touch(exist_ok=False)
    try:
        started = time.monotonic()
        wait_state(False)
        health_observed_ms = (time.monotonic() - started) * 1000
        old = counts()
        with ThreadPoolExecutor(max_workers=1) as pool:
            bulk = pool.submit(transfer, endpoint, DOMAIN_B, 'D', 8 * MIB)
            probes = []
            for _ in range(5):
                active = not bulk.done()
                row = transfer(endpoint, DOMAIN_A, 'D', 1024)
                probes.append({'elapsed_ms': row['elapsed_seconds'] * 1000, 'during_survivor_bulk': active,
                               'sha256_verified': row['sha256_verified']})
            transfer_row = bulk.result(timeout=24)
        delta = [new - past for past, new in zip(old, counts())]
        if delta != [0, 6]:
            raise RuntimeError('failover did not select the surviving backend')
    finally:
        flag.unlink()
        wait_state(True)
    return {'health_observed_ms': health_observed_ms, 'assignments': delta, 'probe_transfers': probes,
            'survivor_download': transfer_row, 'reconnect_ms': quantiles([p['elapsed_ms'] for p in probes])}


def measure_mixed(endpoint, trial):
    before = resources()
    samples = [before['memory_current']]
    stop = threading.Event()

    def sample():
        while not stop.wait(.25):
            samples.append(resources()['memory_current'])

    with ThreadPoolExecutor(max_workers=1) as pool:
        observer = pool.submit(sample)
        try:
            trial['mixed'] = run_mixed(endpoint, 30)
        finally:
            stop.set()
            observer.result(timeout=3)
    after = resources()
    trial['mixed_summary'] = mixed_result(trial['mixed'], 30)
    trial['mixed_cpu_seconds'] = (after['cpu']['usage_usec'] - before['cpu']['usage_usec']) / 1e6
    trial['mixed_peak_group_bytes'] = max(max(samples), after['memory_current'])
    if len(samples) < 2 or trial['mixed_cpu_seconds'] <= 0:
        raise RuntimeError('mixed resource measurements missing')


def compare(original, shaped):
    before, after = (row['mixed_summary'] for row in (original, shaped))
    throughput = {direction: after[direction + '_mbps'] / before[direction + '_mbps'] for direction in ('D', 'U')}
    solo_rates = {direction: shaped['solo']['rates'][direction]['mbps'] / original['solo']['rates'][direction]['mbps']
                  for direction in ('D', 'U')}
    latency = {kind: after[f'loaded_{kind}_ms']['p95'] / before[f'loaded_{kind}_ms']['p95']
               for kind in ('message', 'new_connection')}
    cpu = shaped['mixed_cpu_seconds'] / original['mixed_cpu_seconds']
    memory = shaped['mixed_peak_group_bytes'] / original['mixed_peak_group_bytes']
    sufficient = not (before['sample_limited'] or after['sample_limited'])
    return {'directional_mixed_throughput_ratio': throughput, 'directional_solo_throughput_ratio': solo_rates,
            'mixed_p95_ratio': latency, 'cpu_seconds_ratio': cpu, 'peak_group_memory_ratio': memory,
            'sufficient_loaded_samples': sufficient,
            'joint_gain': sufficient and min(*throughput.values(), *solo_rates.values()) >= .95
            and max(latency.values()) <= .90 and cpu <= 1.10 and memory <= 1.10}


def run_screen(results, shared, counts, wait_state):
    endpoint = SimpleNamespace(front=24433, real_dc=False)
    output = results / 'dual-shared-queue.json'
    report = {'scope': 'private 100ms, 100Mbps shared outer WireGuard link, two backends, synthetic DC',
              'order': ORDER, 'trials': [], 'complete': False, 'qualified': False}
    try:
        wait_state(True)
        for index, policy in enumerate(ORDER):
            domain = DOMAIN_A if index < 2 else DOMAIN_B
            set_policy(policy)
            trial = {'pair': index // 2 + 1, 'policy': policy}
            report['trials'].append(trial)
            trial['queue_before'] = snapshot(policy)
            initial = counts()
            measure_mixed(endpoint, trial)
            trial['mixed_assignments'] = [new - old for old, new in zip(initial, counts())]
            if (min(trial['mixed_assignments']) < 1
                    or sum(trial['mixed_assignments']) != trial['mixed']['dc_connections']):
                raise RuntimeError('mixed connection totals or two-line coverage did not match')
            trial['queue_after_mixed'] = snapshot(policy)
            trial['solo'] = solo(endpoint, counts, domain)
            trial['failover'] = failover(endpoint, shared, counts, wait_state)
            trial['queue_after'] = snapshot(policy)
            save(output, report)
        dc = json.loads((shared / 'dc.json').read_text())['backends']
        report['dc_errors'] = [node['errors'] for node in dc]
        if report['dc_errors'] != [[], []]:
            raise RuntimeError('synthetic DC transfer errors')
        first, second, third, fourth = report['trials']
        report['pairs'] = [compare(first, second), compare(fourth, third)]
        report['complete'] = True
        report['qualified'] = all(pair['joint_gain'] for pair in report['pairs'])
    except Exception as exc:
        report['error'] = {'type': type(exc).__name__, 'phase': str(exc)[:240]}
        raise
    finally:
        save(output, report)
