import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from paired_report import combine
import test_paired_analysis as fixtures


class PairedReportTests(unittest.TestCase):
    def fixture(self):
        reports = []
        for replica in (1, 2):
            profiles = []
            for name in ('baseline', 'sustained', 'impaired'):
                profile = fixtures.PairedAnalysisTests().fixture(1.08)
                profile.update(name=name, payload_bytes=262144, per_stream_rate_bytes_sec=12500000)
                if name != 'baseline':
                    profile['expected_pairs'] = 4;profile['pairs'] = profile['pairs'][:4]
                for pair in profile['pairs']:
                    if replica == 2:pair['order'] = pair['order'][::-1]
                    for run in pair['runs'].values():run['backend_cpu_affinity_verified'] = True
                profiles.append(profile)
            reports.append({'replica': replica, 'source_commit': 'a'*40, 'manager_sha256': 'b'*64,
                            'pins': {'fixture_only': True}, 'complete': True, 'oom_delta': {'oom': 0}, 'profiles': profiles})
        return reports

    def test_only_replicated_ratio_advantage_is_reported(self):
        reports = self.fixture()
        self.assertEqual(combine(reports)['profiles'][0]['cases'][0]['verdict'], 'REPLICATED_FASTER')
        for pair in reports[1]['profiles'][0]['pairs']:
            pair['runs']['stable']['rows'][0]['aggregate_mbps'] = 20
        self.assertEqual(combine(reports)['profiles'][0]['cases'][0]['verdict'], 'NO_REPLICATED_ADVANTAGE')

    def test_oom_wrong_commit_duplicate_replica_missing_profile_and_affinity_are_rejected(self):
        original = self.fixture()
        changes = [('oom_delta', {'oom': 1}), ('source_commit', 'c'*40), ('replica', 1),
                   ('complete', False), ('profiles', original[1]['profiles'][:2])]
        for key, value in changes:
            reports = copy.deepcopy(original);reports[1][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): combine(reports)
        reports = copy.deepcopy(original)
        reports[1]['profiles'][0]['pairs'][0]['runs']['stable']['backend_cpu_affinity_verified'] = False
        with self.assertRaises(ValueError): combine(reports)

    def test_failed_protocol_remains_failure_in_cross_job_summary(self):
        reports = self.fixture()
        reports[1]['profiles'][0]['pairs'][0]['runs']['stable']['accepted'] = False
        result = combine(reports)
        self.assertFalse(result['transport_accepted'])
        self.assertEqual(result['profiles'][0]['cases'][0]['verdict'], 'PROTOCOL_FAILURE')

    def test_cli_exit_status_matches_protocol_result_without_losing_failed_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for failed in (False,True):
                reports=self.fixture()
                if failed:reports[1]['profiles'][0]['pairs'][0]['runs']['stable']['accepted']=False
                inputs=[]
                for index,report in enumerate(reports,1):
                    path=root/f'replica-{index}.json';path.write_text(json.dumps(report));inputs.append(str(path))
                output=root/'summary.json'
                result=subprocess.run([sys.executable,str(Path(__file__).with_name('paired_report.py')),
                    *inputs,'--output',str(output)],capture_output=True,timeout=10)
                self.assertEqual(result.returncode,1 if failed else 0)
                self.assertEqual(json.loads(output.read_text())['transport_accepted'],not failed)


if __name__ == '__main__': unittest.main()
