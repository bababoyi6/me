"""Counterexamples for the split-process experiment's evidence gates."""
import copy
import unittest
from long_path_analysis import analyze, KEYS
from release_pins import TLS_BASELINE, TLS_CANDIDATES, OLD_TLS_RTT_RECHECK_SHA256


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
    def test_focused_tls_rtt_recheck_keeps_identity_samples_and_release_gate_separate(self):
        base = fixture()
        template = next(r for r in base['rows'] if r['rtt_ms']==100 and r['streams']==1 and r['direction']=='D')
        base.update(plan='tls-rtt-recheck', tls_candidate='old',
                    binary_sha256={'balanced2': TLS_BASELINE[1], 'candidate': OLD_TLS_RTT_RECHECK_SHA256},
                    candidate_manifest={'source_commit': TLS_CANDIDATES['old'][2],
                                        'project_commit': 'fixture',
                                        'binaries': {'bulk': {'sha256': OLD_TLS_RTT_RECHECK_SHA256, 'version': TLS_CANDIDATES['old'][3]}}},
                    resources_before={'cpu_max': '300000 100000', 'memory_max': str(1536*1024**2),
                                      'swap_max': '0'},
                    resources_after={'pids_current': 1})
        base['controls'] = [r for r in base['controls'] if r['configured_rtt_ms']==100]
        base['rows'] = []
        for turn in range(1,5):
            order = ('balanced2','candidate') if turn%2 else ('candidate','balanced2')
            for variant in order:
                row = copy.deepcopy(template)
                row.update(round=turn, variant=variant,
                           version=TLS_BASELINE[2] if variant=='balanced2' else TLS_CANDIDATES['old'][3],
                           sha256_verified=True,
                           socket_samples=[{'role':'client','rtt_ms':100},
                                           {'role':'client','rtt_ms':101}])
                base['rows'].append(row)
        result = analyze(base)
        self.assertTrue(result['targeted_rtt_recheck_passed'])
        self.assertFalse(result['production_approval'])
        self.assertEqual(result['measured_payload_flows'],8)
        for change in ('few_samples','slow_rtt','slow_throughput'):
            data = copy.deepcopy(base)
            candidate = next(r for r in data['rows'] if r['variant']=='candidate')
            if change=='few_samples':
                candidate['socket_samples'].pop()
                with self.assertRaisesRegex(ValueError,'measured RTT'):analyze(data)
                continue
            if change=='slow_rtt':
                candidate['socket_samples']=[{'role':'client','rtt_ms':122}]*2
            else:
                candidate['mbps']=94
            self.assertFalse(analyze(data)['targeted_rtt_recheck_passed'])
        data=copy.deepcopy(base);data['rows'].pop()
        with self.assertRaisesRegex(ValueError,'cohort'):analyze(data)
        data=copy.deepcopy(base);data['binary_sha256']['candidate']='a'*64
        with self.assertRaisesRegex(ValueError,'binary changed'):analyze(data)

    def test_tls_parity_requires_both_orders_and_both_performance_limits(self):
        data = fixture()
        data.update(plan='tls-candidate-parity', tls_candidate='old',
                    binary_sha256={'balanced2': TLS_BASELINE[1], 'candidate': 'a'*64},
                    candidate_manifest={'source_commit': TLS_CANDIDATES['old'][2],
                                        'project_commit': 'fixture',
                                        'binaries': {'bulk': {'sha256': 'a'*64, 'version': TLS_CANDIDATES['old'][3]}}},
                    resources_before={'cpu_max': '300000 100000', 'memory_max': str(1536*1024**2),
                                      'swap_max': '0'},
                    resources_after={'pids_current': 1})
        data['controls'] = [r for r in data['controls'] if r['configured_rtt_ms'] == 100]
        template = [r for r in data['rows'] if r['round'] == 1 and r['rtt_ms'] == 100
                    and r['variant'] == 'balanced2']
        data['rows'] = []
        for turn, variants in enumerate((('balanced2', 'candidate'), ('candidate', 'balanced2')), 1):
            for variant in variants:
                version = TLS_BASELINE[2] if variant == 'balanced2' else TLS_CANDIDATES['old'][3]
                data['rows'].extend(dict(row, round=turn, variant=variant, version=version,
                                         sha256_verified=True) for row in template)
        self.assertTrue(analyze(data)['initial_screen_passed'])
        cache = copy.deepcopy(data)
        cache['tls_candidate'] = 'oldcache'
        cache['candidate_manifest']['binaries']['bulk']['version'] = TLS_CANDIDATES['oldcache'][3]
        for row in cache['rows']:
            if row['variant'] == 'candidate':
                row['version'] = TLS_CANDIDATES['oldcache'][3]
        self.assertTrue(analyze(cache)['initial_screen_passed'])
        for field, value in (('mbps', 94), ('socket_samples', [{'role': 'client', 'rtt_ms': 112}])):
            modified = copy.deepcopy(data)
            modified['rows'][4][field] = value
            result = analyze(modified)
            self.assertFalse(result['initial_screen_passed'])
            self.assertFalse(result['production_approval'])
        data['rows'][:8] = data['rows'][4:8] + data['rows'][:4]
        with self.assertRaisesRegex(ValueError, 'AB/BA'):
            analyze(data)

    def test_equal_load_has_its_own_latency_and_instrumentation_gates(self):
        data=fixture();data.update(plan='equal-load',transfer_rate_per_stream=2500000,
            native_manifest={'upstream_commit':'66708d2866f3f3a4541c4fa67984d408e71a8af4'})
        source=copy.deepcopy(data['rows']);data['rows']=[]
        data['controls']=[r for r in data['controls'] if r['configured_rtt_ms']==100]
        for turn in (1,2,3,4):
            for old,new in (('stable','control'),('stable-auto','balanced')):
                for row in source:
                    if row['rtt_ms']==100 and row['round']==1 and row['variant']==old and row['direction']=='D':
                        changed=copy.deepcopy(row);changed.update(round=turn,variant=new,mbps=20,
                            version='telemt 3.5.17+tdh.'+('control1' if new=='control' else 'balanced1'))
                        changed['cpu_percent_one_core']['probe_children']=0
                        changed['path_probe']={'failures':[],'samples':[{'rtt_ms':120}]*6}
                        data['rows'].append(changed)
        result=analyze(data)
        self.assertEqual(result['measured_payload_flows'],40)
        self.assertTrue(all(r['classification']=='EQUAL_LOAD_WITHIN_BOUNDS' for r in result['comparisons']))
        bad=copy.deepcopy(data);bad['rows'][0]['cpu_percent_one_core'].pop('probe_children')
        with self.assertRaisesRegex(ValueError,'probe CPU'):analyze(bad)
        bad=copy.deepcopy(data);bad['transfer_rate_per_stream']=125000000
        with self.assertRaisesRegex(ValueError,'offered-load'):analyze(bad)
        bad=copy.deepcopy(data)
        for r in bad['rows']:r['path_probe']['samples']=[{'rtt_ms':170}]*6
        self.assertTrue(all(r['classification']=='EQUAL_LOAD_REVIEW' for r in analyze(bad)['comparisons']))

    def test_new_official_release_is_explicit_and_unknown_release_is_rejected(self):
        data=fixture();data['official_release']='3.5.17'
        for row in data['rows']:
            if row['variant']!='balanced2':row['version']='telemt 3.5.17'
        self.assertTrue(analyze(data)['transport_accepted'])
        data['official_release']='unverified'
        with self.assertRaises(ValueError):analyze(data)

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
