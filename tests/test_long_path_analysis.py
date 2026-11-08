"""Counterexamples for the split-process experiment's evidence gates."""
import copy
import unittest
from long_path_analysis import analyze, KEYS


def fixture():
    return {'accepted':True,'complete':True,'namespaces_removed':True,'private_processes_closed':True,
        'cleanup_errors':[],'oom_delta':{'oom':0,'oom_kill':0},'payload_bytes_per_stream':32*1024**2,
        'rate_mbps':200,'source_commit':'fixture','cpu_assignment':{'core':0},
        'controls':[{'configured_rtt_ms':r,'direction':d,'receiver_mbps':190} for r in (0,100) for d in ('U','D')],
        'rows':[{'rtt_ms':r,'round':t,'variant':v,'streams':n,'direction':d,'completed':n,'errors':[],
            'version':'telemt 3.5.5+tdh.balanced2' if v=='balanced2' else 'telemt 3.5.16',
            'config_policy_sha256':'a'*64,'mbps':120 if v=='stable-auto' else 100,'wall_seconds':3,
            'cpu_percent_one_core':{k:10 for k in ('backend','client','dc','haproxy','controller')},
            'backend_thread_affinities':[[0]],'socket_samples':[{'role':'client','rtt_ms':r+.1}]}
            for r,t,v,n,d in sorted(KEYS)]}


class LongPathAnalysisTests(unittest.TestCase):
    def test_native_four_pair_plan_rejects_missing_round(self):
        data=fixture();data['plan']='native'
        data['native_manifest']={'upstream_commit':'84e5b827a6619fb2e09ad3d09f253cdffe04bc2f'}
        data['controls']=[r for r in data['controls'] if r['configured_rtt_ms']==100]
        original=data['rows'];data['rows']=[]
        for t in (1,2,3,4):
            for old,new,version in (('stable','control','control1'),('stable-auto','balanced','balanced1')):
                for row in original:
                    if row['rtt_ms']==100 and row['round']==1 and row['variant']==old:
                        changed=copy.deepcopy(row);changed.update(round=t,variant=new,version='telemt 3.5.16+tdh.'+version)
                        data['rows'].append(changed)
        result=analyze(data)
        self.assertEqual(result['measured_payload_flows'],80)
        self.assertEqual(len(result['comparisons']),4)
        self.assertTrue(all(r['classification']=='REPEATABLE_FASTER' for r in result['comparisons']))
        data['rows'].pop()
        with self.assertRaises(ValueError):analyze(data)

    def test_exact_workload_and_exploratory_threshold(self):
        result=analyze(fixture())
        self.assertEqual(result['measured_payload_flows'],180)
        self.assertEqual(len(result['comparisons']),16)
        self.assertEqual({r['classification'] for r in result['comparisons'] if r['candidate']=='stable-auto'},{'REPEATABLE_FASTER'})
        self.assertEqual({r['classification'] for r in result['comparisons'] if r['candidate']=='stable'},{'NO_CLEAR_CHANGE'})

    def test_partial_duplicate_drift_bad_cpu_missing_rtt_and_affinity_fail_closed(self):
        base=fixture()
        for field,value in (('completed',0),('config_policy_sha256','b'*64),('mbps',float('nan')),
                ('cpu_percent_one_core',{}),('socket_samples',[]),('backend_thread_affinities',[[1]])):
            data=copy.deepcopy(base);data['rows'][0][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):analyze(data)
        for rows in (base['rows'][:-1],base['rows']+[base['rows'][0]]):
            data=copy.deepcopy(base);data['rows']=rows
            with self.assertRaises(ValueError):analyze(data)
        data=fixture();data['private_processes_closed']=False
        with self.assertRaises(ValueError):analyze(data)

    def test_cpu_capacity_and_measured_rtt_override_apparent_speed(self):
        for modifier,label in (('cpu','HARNESS_CPU_LIMITED'),('path','PATH_CONTROL_LIMITED'),('rtt','RTT_NOT_ESTABLISHED')):
            data=fixture()
            if modifier=='cpu':
                for r in data['rows']:r['cpu_percent_one_core']['client']=80
            elif modifier=='path':
                for r in data['controls']:r['receiver_mbps']=80
            else:
                for r in data['rows']:r['socket_samples']=[{'role':'client','rtt_ms':0.1}]
            result=analyze(data)
            self.assertTrue(all(r['classification']==label for r in result['comparisons'] if modifier!='rtt' or r['rtt_ms']==100))
