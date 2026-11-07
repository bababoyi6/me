"""Fail-closed, within-job comparisons; never combine absolute speeds across hosts."""
import math
import re
import statistics

KINDS = ('balanced2', 'stable')
CASES = tuple((n, d) for n in (30, 50, 100) for d in ('U', 'D'))


def schedule(pairs, replica):
    if pairs not in (4, 6) or replica not in (1, 2):
        raise ValueError('bounded, balanced schedule required')
    return [KINDS if (i + replica) % 2 else KINDS[::-1] for i in range(pairs)]


def positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def analyze(profile):
    count = profile['expected_pairs']
    if count not in (4, 6):
        raise ValueError('unexpected pair count')
    pairs = profile.get('pairs', [])
    if len(pairs) != count:
        raise ValueError('incomplete profile')
    policy = set()
    indexed = []
    if sum(tuple(p.get('order', [])) == KINDS for p in pairs) != count//2:
        raise ValueError('both orders must be equally represented')
    for i, pair in enumerate(pairs):
        if pair.get('pair') != i + 1 or tuple(pair.get('order', [])) not in (KINDS, KINDS[::-1]):
            raise ValueError('invalid pair identity/order')
        runs = pair.get('runs', {})
        if set(runs) != set(KINDS):
            raise ValueError('both core runs required')
        index = {}
        for kind, run in runs.items():
            if run.get('version') != {'balanced2': 'telemt 3.5.5+tdh.balanced2', 'stable': 'telemt 3.5.16'}[kind]:
                raise ValueError('core identity mismatch')
            if (run.get('config_unchanged') is not True or run.get('offline_bootstrap') is not True or
                    run.get('private_processes_closed') is not True or
                    run.get('mock_dc_socket_timeout_seconds') != 25 or run.get('client_io_timeout_seconds') != 25):
                raise ValueError('incomparable or uncleared run')
            policy_hash = run['config_policy_sha256']
            if not isinstance(policy_hash, str) or not re.fullmatch('[a-f0-9]{64}', policy_hash):
                raise ValueError('invalid configuration identity')
            policy.add(policy_hash)
            rows = run.get('rows', [])
            keys = [(r['concurrency'], r['direction']) for r in rows]
            if len(keys) != len(CASES) or set(keys) != set(CASES):
                raise ValueError('duplicate/missing cohort')
            index[kind] = dict(zip(keys, rows))
        indexed.append(index)
    if len(policy) != 1 or not next(iter(policy)):
        raise ValueError('configuration policy differs between runs')
    valid_runs = all(p['runs'][k].get('accepted') is True for p in pairs for k in KINDS)
    cases = []
    for key in CASES:
        old = [p['balanced2'][key] for p in indexed]
        new = [p['stable'][key] for p in indexed]
        case = {'concurrency': key[0], 'direction': key[1], 'pairs': count,
                'old_successes': sum(r['successes'] for r in old),
                'new_successes': sum(r['successes'] for r in new), 'expected_per_core': count*key[0]}
        # Do not turn partial transfers into a throughput win or drop failed pairs.
        if not valid_runs or any(r['errors'] or r['successes'] != key[0] for r in old+new):
            case.update(classification='TRANSFER_FAILURE', throughput_ratio=None)
        else:
            ratios, tails = [], []
            for a, b in zip(old, new):
                values = (a['aggregate_mbps'], b['aggregate_mbps'], a['completion_ms']['p95'], b['completion_ms']['p95'])
                if not all(positive(v) for v in values):
                    raise ValueError('non-finite or non-positive metric')
                ratios.append(values[1]/values[0]); tails.append(values[3]/values[2])
            median, tail = statistics.median(ratios), statistics.median(tails)
            limited = any(r.get('controller_and_mock_cpu_one_core_percent', 0) >= 70 for r in old+new)
            classification = ('HARNESS_CPU_LIMITED' if limited else
                              'REPEATABLE_FASTER' if min(ratios) > 1.05 and max(tails) <= 1.10 else
                              'REPEATABLE_SLOWER' if max(ratios) < .95 else
                              'REGRESSION_REVIEW' if median < .95 or tail > 1.10 else 'NO_CLEAR_CHANGE')
            case.update(classification=classification,
                        throughput_ratio={'median': median, 'min': min(ratios), 'max': max(ratios), 'all_pairs': ratios},
                        completion_p95_ratio={'median': tail, 'all_pairs': tails},
                        old_median_mbps=statistics.median(r['aggregate_mbps'] for r in old),
                        new_median_mbps=statistics.median(r['aggregate_mbps'] for r in new))
        cases.append(case)
    # Even a transport pass does not prove sustained speed or long-term reliability.
    return {'complete': True, 'transport_accepted': valid_runs and all(c['classification'] != 'TRANSFER_FAILURE' for c in cases),
            'policy_equal': True, 'cases': cases,
            'decision_rule': 'all paired throughput ratios >1.05 and p95 ratios <=1.10 for faster; exploratory engineering threshold, not proof of equivalence or population significance'}
