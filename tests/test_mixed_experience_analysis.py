"""Synthetic analysis fixtures test rejection rules, not proxy performance."""
import copy
import unittest

from mixed_experience_analysis import analyze, quantiles, VARIANTS
from legacy_queue_analysis import NATIVE_SHA, LEGACY_SHA
from release_pins import RELEASES


def fixture():
    rows=[]
    for turn in range(3):
        for variant in VARIANTS[turn:]+VARIANTS[:turn]:
            for cohort in ((30,50,100) if turn%2==0 else (100,50,30)):
                result={'logical_clients':cohort,'idle_connections':cohort-11,'idle_recovery_ms':[100]*(cohort-11),
                    'complete':True,'errors':[],'dc_connections':cohort+36,
                    'bulk':[{'direction':d,'bytes':16*1024**2,'sha256_verified':True,'persistent_dc':True,
                             'elapsed_seconds':1,'start_offset_seconds':1,'end_offset_seconds':2} for d in ('D','D','D','D','U')]}
                for phase in ('baseline','loaded'):
                    result[phase]={name:[{'elapsed_ms':100,'active_bulk':5 if phase=='loaded' else 0,
                        'offset_seconds':i/10,'sha256_verified':True} for i in range(20)] for name in ('message','new_connection')}
                rows.append({'round':turn+1,'variant':variant,'cohort':cohort,'result':result,
                    'cpu_percent_one_core':dict.fromkeys(('backend','client','dc','haproxy','controller','observer_children'),5),
                    'process_cpu_intervals':[{'percent':dict.fromkeys(('backend','client','dc','haproxy'),5)}],
                    'socket_samples':[{'rows':[{'role':'client','rtt_ms':100}]}],
                    'qdisc_samples':[{'rows':[{'kind':'netem','backlog':1000}]}],
                    'sampled_backend_peak_rss':64*1024**2,'config_policy_sha256':'a'*64,'backend_thread_affinities':[[0]]})
    return {'plan':'mixed-experience-v1','accepted':True,'complete':True,'profile':'rtt100',
        'rtt_ms':100,'rate_mbps':200,'loss_percent':0,'cleanup_errors':[],'oom_delta':{'oom':0,'oom_kill':0},
        'private_processes_closed':True,'namespaces_removed':True,'cpu_assignment':{'core':0},
        'resources_before':{'cpu_max':'300000 100000','memory_max':str(1536*1024**2),'swap_max':'0'},
        'resources_after':{'pids_current':1,'memory_peak':200*1024**2},
        'binary_sha256':{'balanced2':LEGACY_SHA,'official17':RELEASES['3.5.17'][1],'native17':NATIVE_SHA},
        'controls':[{'direction':d,'mbps':180} for d in ('D','U')],
        'rows':rows,'mock_dc':{'completed':sum(r['result']['dc_connections'] for r in rows)+36,'errors':[]}}


class MixedExperienceAnalysisTests(unittest.TestCase):
    def test_full_joint_parity_is_separate_from_production_approval(self):
        result=analyze(fixture())
        self.assertTrue(result['performance_parity_all_cases'])
        self.assertFalse(result['production_approval'])
        self.assertFalse(quantiles(range(1,21))['p99_population_inference'])

    def test_missing_evidence_or_silent_corruption_is_rejected(self):
        for mutation in ('payload','digest','observation','schedule','pin','config','affinity','dc','nan','budget'):
            data=fixture();row=data['rows'][0]
            if mutation=='payload':row['result']['bulk'][0]['bytes']=1
            if mutation=='digest':row['result']['loaded']['new_connection'][0]['sha256_verified']=False
            if mutation=='observation':row['socket_samples']=[]
            if mutation=='schedule':data['rows'].pop()
            if mutation=='pin':data['binary_sha256']['official17']='a'*64
            if mutation=='config':row['config_policy_sha256']='b'*64
            if mutation=='affinity':row['backend_thread_affinities']=[[1]]
            if mutation=='dc':data['mock_dc']['completed']-=1
            if mutation=='nan':row['result']['baseline']['message'][0]['elapsed_ms']=float('nan')
            if mutation=='budget':data['resources_before']['swap_max']='1'
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):analyze(data)

    def test_cpu_capacity_and_sample_limits_cannot_be_called_an_improvement(self):
        for mutation in ('cpu','capacity','sample'):
            data=fixture()
            if mutation=='capacity':data['controls'][0]['mbps']=80
            for row in data['rows']:
                if mutation=='cpu':row['process_cpu_intervals'][0]['percent']['client']=80
                if mutation=='sample':row['result']['loaded']['message']=row['result']['loaded']['message'][:5]
            self.assertFalse(analyze(data)['performance_parity_all_cases'])

    def test_one_bad_pair_or_absolute_queue_delay_blocks_joint_parity(self):
        for mutation in ('throughput','latency','rss','queue'):
            data=fixture();row=next(r for r in data['rows'] if r['variant']=='native17')
            if mutation=='throughput':
                for v in row['result']['bulk']:v.update(elapsed_seconds=2,end_offset_seconds=3)
            if mutation=='latency':
                for v in row['result']['loaded']['message']:v['elapsed_ms']=111
            if mutation=='rss':row['sampled_backend_peak_rss']*=2
            if mutation=='queue':
                for other in data['rows']:
                    for v in other['result']['loaded']['message']:v['elapsed_ms']=250
            self.assertFalse(analyze(data)['performance_parity_all_cases'])
