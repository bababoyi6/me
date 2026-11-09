"""Do not advance a fairness idea on missing data or a throughput/CPU tradeoff."""
import copy
import unittest
from test_mixed_experience_analysis import fixture
from fair_screen_analysis import analyze
from legacy_queue_analysis import NATIVE_SHA, UPSTREAM


def sample():
    data=fixture();row=data['rows'][0];data.update(plan='fair-quantum-screen-v1',source_commit='f'*40)
    data['fair_manifest']={'upstream_commit':UPSTREAM,'reference_binary_sha256':NATIVE_SHA,'manager_commit':'f'*40,
        'quantum_bytes':65536,'version':'telemt 3.5.17+tdh.fair1','production_enabled':False,'binary_sha256':'a'*64}
    data['binary_sha256']={'native17':NATIVE_SHA,'fair17':'a'*64};data['rows']=[]
    for turn in range(4):
        for variant in (('native17','fair17') if turn%2==0 else ('fair17','native17')):
            new=copy.deepcopy(row);new.update(round=turn+1,variant=variant)
            if variant=='fair17':
                for kind,latency in (('message',80),('new_connection',90)):
                    for v in new['result']['loaded'][kind]:v['elapsed_ms']=latency
            data['rows'].append(new)
    data['mock_dc']['completed']=sum(r['result']['dc_connections'] for r in data['rows'])+32
    return data


class FairScreenTests(unittest.TestCase):
    def test_candidate_version_is_registered_before_expensive_build(self):
        from build_fair_candidate import VERSION
        from e2e_lab import FullLab
        self.assertIn(VERSION,FullLab.EXPERIMENTAL_VERSIONS)
    def test_all_four_joint_wins_only_advance_to_qualification(self):
        result=analyze(sample())
        self.assertTrue(result['advance_to_qualification']);self.assertFalse(result['production_approved'])

    def test_one_latency_cpu_or_capacity_failure_blocks_advance(self):
        for change in ('latency','cpu','control','sample'):
            data=sample();row=data['rows'][1]
            if change=='latency':
                for v in row['result']['loaded']['message']:v['elapsed_ms']=81
            if change=='cpu':row['cpu_percent_one_core']['backend']=6
            if change=='control':data['controls'][0]['mbps']=80
            if change=='sample':row['result']['loaded']['new_connection'].pop()
            self.assertFalse(analyze(data)['advance_to_qualification'])

    def test_partial_schedule_and_changed_identity_are_rejected(self):
        for change in ('schedule','binary'):
            data=sample()
            if change=='schedule':data['rows'].pop()
            if change=='binary':data['binary_sha256']['native17']='b'*64
            with self.assertRaises(ValueError):analyze(data)
