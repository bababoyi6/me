"""Memory/FD/protocol failures cannot be relabelled as resource acceptance."""
import copy
import unittest
from stress_analysis import assess


def fixture():
    return {'plan':'sustained','accepted':True,'complete':True,'private_processes_closed':True,'namespaces_removed':True,
        'cleanup_errors':[],'oom_delta':{'oom':0,'oom_kill':0},'payload_bytes_per_stream':4*1024**2,'rate_mbps':200,
        'resources_before':{'cpu_max':'300000 100000','memory_max':str(768*1024**2),'swap_max':'0'},
        'resources_after':{'memory_peak':300*1024**2,'pids_current':1},'source_commit':'fixture',
        'native_manifest':{'upstream_commit':'66708d2866f3f3a4541c4fa67984d408e71a8af4'},'cpu_assignment':{'core':0},
        'rows':[{'variant':v,'streams':n,'direction':d,'completed':n,'errors':[],'rtt_ms':100,
            'version':'telemt 3.5.17+tdh.'+('control1' if v=='control' else 'balanced1'),
            'config_policy_sha256':'a'*64,'backend_thread_affinities':[[0]],'harness_limited':True}
            for v in ('control','balanced') for n in (30,50,100) for d in ('U','D')],
        'idle_checks':[{'variant':v,'before':{'fds':14},'after':{'fds':14}} for v in ('control','balanced')],
        'mock_dc':{'completed':728,'errors':[]}}


class StressAnalysisTests(unittest.TestCase):
    def test_all_payloads_and_resource_bounds_pass_without_speed_claim(self):
        result=assess(fixture())
        self.assertEqual(result['payload_flows'],720)
        self.assertEqual(result['generator_limited_batches'],12)
        self.assertNotIn('throughput_ratio',result)
        self.assertFalse(result['flow_timing_available'])
        data=fixture()
        for row in data['rows']:row['completion_ms']={'p50':1000,'p95':1200}
        self.assertTrue(assess(data)['flow_timing_available'])
        data['rows'][0]['completion_ms']['p95']=float('nan')
        with self.assertRaisesRegex(ValueError,'completion latency'):assess(data)

    def test_missing_failed_duplicate_and_configuration_drift_are_rejected(self):
        base=fixture()
        for key,value in (('completed',0),('errors',[{'type':'TimeoutError'}]),('config_policy_sha256','b'*64),
                ('backend_thread_affinities',[[1]])):
            data=copy.deepcopy(base);data['rows'][0][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):assess(data)
        data=fixture();data['rows'].pop()
        with self.assertRaises(ValueError):assess(data)
        data=fixture();data['rows'][0]=data['rows'][1]
        with self.assertRaises(ValueError):assess(data)

    def test_oom_wrong_budget_residual_process_or_descriptor_growth_is_rejected(self):
        for field in ('oom','memory','pid','fd','budget'):
            data=fixture()
            if field=='oom':data['oom_delta']['oom']=1
            if field=='memory':data['resources_after']['memory_peak']=769*1024**2
            if field=='pid':data['resources_after']['pids_current']=2
            if field=='fd':data['idle_checks'][0]['after']['fds']=17
            if field=='budget':data['resources_before']['memory_max']=str(1536*1024**2)
            with self.subTest(field=field),self.assertRaises(ValueError):assess(data)
