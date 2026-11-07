"""Revalidate both raw reports, then compare within-host ratios across replicas."""
import argparse
import hashlib
import json
from pathlib import Path
import re

from paired_analysis import analyze


def combine(reports):
    if len(reports) != 2 or {r.get('replica') for r in reports} != {1, 2}:
        raise ValueError('two distinct replicas required')
    first, second = sorted(reports, key=lambda r: r['replica'])
    for field in ('source_commit', 'manager_sha256', 'pins'):
        if not first.get(field) or first[field] != second.get(field):
            raise ValueError('source/binary identity mismatch between jobs')
    if not re.fullmatch('[a-f0-9]{40}', first['source_commit']):
        raise ValueError('immutable source required')
    indexes = []
    for report in (first, second):
        if report.get('complete') is not True or any(report.get('oom_delta', {'missing': 1}).values()):
            raise ValueError('incomplete or OOM-interrupted task')
        profiles = report.get('profiles', [])
        if len(profiles) != 3 or {p['name'] for p in profiles} != {'baseline', 'sustained', 'impaired'}:
            raise ValueError('all predeclared conditions required')
        index = {}
        for profile in profiles:
            expected = 6 if profile['name'] == 'baseline' else 4
            if profile['expected_pairs'] != expected:
                raise ValueError('pair count changed')
            for pair in profile['pairs']:
                for run in pair['runs'].values():
                    if run.get('backend_cpu_affinity_verified') is not True:
                        raise ValueError('unverified backend affinity')
            index[profile['name']] = (profile, analyze(profile))
        indexes.append(index)
    summary = {'source_commit': first['source_commit'], 'manager_sha256': first['manager_sha256'],
               'scope': 'replicated within-job ratios; absolute Mbps are not pooled across hosts',
               'complete': True, 'transport_accepted': True, 'profiles': []}
    for name in ('baseline', 'sustained', 'impaired'):
        a, b = indexes[0][name], indexes[1][name]
        if any(a[0][k] != b[0][k] for k in ('payload_bytes', 'per_stream_rate_bytes_sec')):
            raise ValueError('workload differs between replicas')
        summary['transport_accepted'] &= a[1]['transport_accepted'] and b[1]['transport_accepted']
        cases = []
        for x, y in zip(a[1]['cases'], b[1]['cases']):
            if (x['concurrency'], x['direction']) != (y['concurrency'], y['direction']):
                raise ValueError('case identity mismatch')
            labels = [x['classification'], y['classification']]
            verdict = ('PROTOCOL_FAILURE' if 'TRANSFER_FAILURE' in labels else
                       'HARNESS_LIMITED' if 'HARNESS_CPU_LIMITED' in labels else
                       'REPLICATED_FASTER' if labels == ['REPEATABLE_FASTER']*2 else
                       'REPLICATED_SLOWER' if labels == ['REPEATABLE_SLOWER']*2 else
                       'REGRESSION_REVIEW' if any(s in labels for s in ('REPEATABLE_SLOWER', 'REGRESSION_REVIEW')) else
                       'NO_REPLICATED_ADVANTAGE')
            cases.append({'concurrency': x['concurrency'], 'direction': x['direction'], 'verdict': verdict,
                          'replica_1': x, 'replica_2': y})
        summary['profiles'].append({'name': name, 'cases': cases})
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inputs', nargs=2)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    reports = [json.loads(Path(p).read_text()) for p in args.inputs]
    summary = combine(reports)
    summary['input_sha256'] = {Path(p).name: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in args.inputs}
    Path(args.output).write_text(json.dumps(summary, indent=2, allow_nan=False)+'\n')
    print(json.dumps({'complete': True, 'transport_accepted': summary['transport_accepted']}))


if __name__ == '__main__': main()
