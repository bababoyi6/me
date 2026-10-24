from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from analyze_socket_policy import MODES, summarize
sys.path.pop(0)


def fixture():
    data={'classification':'SINGLE_VPS_SOCKET_POLICY_CAUSAL','socket_policy_cases':list(MODES),
          'rounds':2,'complete':True,'payload_bytes_total_per_trial':1024,'rate_mbps_per_direction':100,'rows':[]}
    for turn in (1,2):
        for index,mode in enumerate(MODES):
            row={'build':'flush','streams':8,'direction':'U','expected_payload_bytes':1024,'link_rate_mbps':100,
                 'complete':True,'measurement_complete':True,'socket_policy':mode,'mbps':100+index*10,
                 'small_probe_errors':[]}
            data['rows'].append({'build':'flush','streams':8,'socket_policy':mode,'pair_round':turn,
                                 'exit':0,'trial':{'rows':[row]}})
    return data


class SocketPolicyAnalysisTests(unittest.TestCase):
    def test_declared_two_policy_subsets_are_counterbalanced(self):
        for variant in ('client-auto','dc-auto','auto-lowat'):
            data=fixture()
            data['socket_policy_cases']=['pass',variant]
            data['rows']=[r for r in data['rows'] if r['socket_policy'] in ('pass','client-auto')]
            for record in data['rows']:
                if record['socket_policy']=='client-auto':
                    record['socket_policy']=variant
                    record['trial']['rows'][0]['socket_policy']=variant
            with self.subTest(variant=variant):
                group=summarize(data)['groups'][0]
                self.assertTrue(group['complete_counterbalanced_cases'])
                self.assertAlmostEqual(group['change_percent_vs_pass'][variant],20)

    def test_complete_same_binary_cases_can_compare(self):
        group=summarize(fixture())['groups'][0]
        self.assertTrue(group['complete_counterbalanced_cases'])
        self.assertAlmostEqual(group['change_percent_vs_pass']['auto'],10)

    def test_missing_failed_duplicate_and_invalid_measurement_suppress_all_ratios(self):
        for kind in ('missing','failed','duplicate','late','incomplete','probe'):
            data=fixture()
            if kind=='missing': data['rows'].pop()
            elif kind=='failed': data['rows'][0]['exit']=1
            elif kind=='duplicate': data['rows'][0]['pair_round']=2
            elif kind=='late': data['rows'][0]['trial']['rows'][0]['measurement_complete']=False
            elif kind=='incomplete': data['complete']=False
            else: data['rows'][0]['trial']['rows'][0]['small_probe_errors']=[{'type':'TimeoutError'}]
            with self.subTest(kind=kind):
                self.assertTrue(all(v is None for v in summarize(data)['groups'][0]['change_percent_vs_pass'].values()))

    def test_workload_or_binary_mismatch_is_rejected(self):
        for key,value in (('build','auto'),('socket_policy','other'),('expected_payload_bytes',2048),('mbps',float('nan'))):
            data=fixture()
            data['rows'][0]['trial']['rows'][0][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError): summarize(data)
