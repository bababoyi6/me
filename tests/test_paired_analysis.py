"""Counterexamples prevent cherry-picking, configuration drift and false speed wins."""
import copy
import math
import unittest
from paired_analysis import analyze, schedule, CASES


class PairedAnalysisTests(unittest.TestCase):
    def test_missing_or_invalid_generator_cpu_cannot_hide_a_bottleneck(self):
        for value in (None, float('nan'), float('inf'), -1, True):
            data = self.fixture(1.5)
            data['pairs'][0]['runs']['stable']['rows'][0]['controller_and_mock_cpu_one_core_percent'] = value
            with self.assertRaisesRegex(ValueError,'load-generator CPU'): analyze(data)

    def fixture(self, ratio=1.0):
        profile = {'expected_pairs': 6, 'pairs': []}
        for i, order in enumerate(schedule(6, 1), 1):
            runs = {}
            for kind in order:
                runs[kind] = {'version': 'telemt 3.5.16' if kind == 'stable' else 'telemt 3.5.5+tdh.balanced2',
                    'config_unchanged': True, 'offline_bootstrap': True, 'private_processes_closed': True,
                    'mock_dc_socket_timeout_seconds': 25, 'client_io_timeout_seconds': 25,
                    'config_policy_sha256': 'a'*64, 'accepted': True,
                    'rows': [{'concurrency': n, 'direction': d, 'successes': n, 'errors': [],
                              'aggregate_mbps': 20*(ratio if kind == 'stable' else 1),
                              'completion_ms': {'p95': 100}, 'controller_and_mock_cpu_one_core_percent': 10}
                             for n, d in CASES]}
            profile['pairs'].append({'pair': i, 'order': order, 'runs': runs})
        return profile

    def test_orders_balanced_and_reversed_on_other_replica(self):
        for pairs in (4, 6):
            a, b = schedule(pairs, 1), schedule(pairs, 2)
            self.assertEqual(a.count(('balanced2', 'stable')), pairs//2)
            self.assertEqual(b, [x[::-1] for x in a])
        for pairs, replica in ((3, 1), (6, 0), (100, 2)):
            with self.assertRaises(ValueError): schedule(pairs, replica)

    def test_matched_speed_is_not_declared_a_win(self):
        result = analyze(self.fixture())
        self.assertTrue(result['transport_accepted'])
        self.assertTrue(all(c['classification'] == 'NO_CLEAR_CHANGE' for c in result['cases']))

    def test_repeatable_advantage_and_regression_use_predeclared_threshold(self):
        self.assertEqual(analyze(self.fixture(1.08))['cases'][0]['classification'], 'REPEATABLE_FASTER')
        self.assertEqual(analyze(self.fixture(.90))['cases'][0]['classification'], 'REPEATABLE_SLOWER')
        data = self.fixture(1.08)
        data['pairs'][0]['runs']['stable']['rows'][0]['completion_ms']['p95'] = 150
        self.assertNotEqual(analyze(data)['cases'][0]['classification'], 'REPEATABLE_FASTER')

    def test_failure_suppresses_ratios_without_dropping_pairs(self):
        data = self.fixture(1.5)
        data['pairs'][0]['runs']['balanced2']['rows'][0].update(successes=29, errors=[{'type': 'TimeoutError'}])
        result = analyze(data)
        self.assertFalse(result['transport_accepted'])
        self.assertIsNone(result['cases'][0]['throughput_ratio'])
        self.assertEqual(result['cases'][0]['old_successes'], 179)
        data = self.fixture(1.5);data['pairs'][0]['runs']['stable']['accepted'] = False
        self.assertTrue(all(c['throughput_ratio'] is None for c in analyze(data)['cases']))

    def test_incomplete_drift_duplicate_unknown_version_and_asymmetric_timeout_fail_closed(self):
        original = self.fixture()
        variants = []
        data = copy.deepcopy(original);data['pairs'].pop();variants.append(data)
        data = copy.deepcopy(original);data['pairs'][0]['runs']['stable']['config_policy_sha256'] = 'b'*64;variants.append(data)
        data = copy.deepcopy(original);data['pairs'][0]['runs']['stable']['rows'].append(copy.deepcopy(data['pairs'][0]['runs']['stable']['rows'][0]));variants.append(data)
        for key, value in (('version', 'unknown'), ('mock_dc_socket_timeout_seconds', 8), ('private_processes_closed', False)):
            data = copy.deepcopy(original);data['pairs'][0]['runs']['stable'][key] = value;variants.append(data)
        for data in variants:
            with self.assertRaises(ValueError): analyze(data)

    def test_nan_and_load_generator_saturation_cannot_become_core_advantage(self):
        data = self.fixture(1.5)
        data['pairs'][0]['runs']['stable']['rows'][0]['aggregate_mbps'] = math.nan
        with self.assertRaises(ValueError): analyze(data)
        data = self.fixture(1.5)
        data['pairs'][0]['runs']['stable']['rows'][0]['controller_and_mock_cpu_one_core_percent'] = 80
        self.assertEqual(analyze(data)['cases'][0]['classification'], 'HARNESS_CPU_LIMITED')


if __name__ == '__main__': unittest.main()
