"""Reject misleading old/new parity from missing data or a changed router model."""
import unittest
from legacy_queue_analysis import analyze, ORDERS, LIMITS, NATIVE_SHA, LEGACY_SHA, UPSTREAM, BUILD_SOURCE


def fixture():
    data={'plan':'legacy-queue','accepted':True,'complete':True,'private_processes_closed':True,'namespaces_removed':True,
        'cleanup_errors':[],'oom_delta':{'oom':0,'oom_kill':0},'payload_bytes_per_stream':32*1024**2,
        'rate_mbps':200,'transfer_rate_per_stream':125000000,'source_commit':'fixture','cpu_assignment':{'core':0},
        'resources_before':{'cpu_max':'300000 100000','memory_max':str(1536*1024**2),'swap_max':'0'},
        'resources_after':{'memory_peak':100*1024**2,'pids_current':1},'mock_dc':{'completed':144,'errors':[]},
        'native_manifest':{'upstream_commit':UPSTREAM,'manager_commit':BUILD_SOURCE,'binaries':{'balanced':{'sha256':NATIVE_SHA}}},
        'legacy_binary':{'sha256':LEGACY_SHA},'rows':[],
        'controls':[{'configured_rtt_ms':100,'queue_packet_limit':n,'direction':d,'receiver_mbps':180} for n in LIMITS.values() for d in ('U','D')]}
    for t,order in enumerate(ORDERS):
        for v in order:
            for n in ((1,4) if t%2==0 else (4,1)):
                rtt=120 if v.endswith('bounded') or n==1 else 220
                data['rows'].append({'round':t+1,'variant':v,'streams':n,'direction':'D','rtt_ms':100,
                    'queue_packet_limit':LIMITS[v.split('-')[1]],'completed':n,'errors':[],'mbps':150,
                    'router_packet_limits':{dev:LIMITS[v.split('-')[1]] for dev in ('tdh-la1','tdh-lb0')},
                    'version':'telemt 3.5.5+tdh.balanced2' if v.startswith('legacy-') else 'telemt 3.5.17+tdh.balanced1',
                    'cpu_percent_one_core':{k:5 for k in ('client','haproxy','controller','probe_children','backend','dc')},
                    'backend_thread_affinities':[[0]],'config_policy_sha256':'a'*64,
                    'socket_samples':[{'role':'client','rtt_ms':rtt}],
                    'path_probe':{'failures':[],'samples':[{'rtt_ms':rtt}]*8},
                    'qdisc_samples':[{'rows':[{'kind':'netem','dev':'tdh-la1','backlog':2000,'qlen':2,'drops':0}]}]})
    return data


class LegacyQueueAnalysisTests(unittest.TestCase):
    def test_legacy_parity_does_not_require_slow_official_control_latency(self):
        result=analyze(fixture())
        self.assertTrue(result['legacy_parity_all_cases'])
        self.assertTrue(all(c['classification']=='MODEL_QUEUE_GAIN' for c in result['comparisons'] if c['kind']=='router-model'))

    def test_one_bad_pair_blocks_aggregate_success(self):
        for field,value in (('mbps',100),('socket_samples',[{'role':'client','rtt_ms':350}]),
                ('path_probe',{'failures':[],'samples':[{'rtt_ms':350}]*8})):
            data=fixture();next(r for r in data['rows'] if r['variant']=='native-deep' and r['streams']==4)[field]=value
            self.assertFalse(analyze(data)['legacy_parity_all_cases'])

    def test_missing_and_drifted_evidence_is_rejected(self):
        for mutation in ('row','order','binary','queue','router','config','cpu','counters','dc','affinity','budget','nan'):
            data=fixture();row=data['rows'][0]
            if mutation=='row':data['rows'].pop()
            if mutation=='order':data['rows'][0],data['rows'][1]=data['rows'][1],data['rows'][0]
            if mutation=='binary':data['native_manifest']['binaries']['balanced']['sha256']='0'*64
            if mutation=='queue':row['queue_packet_limit']=999
            if mutation=='router':row['router_packet_limits']['tdh-la1']=999
            if mutation=='config':row['config_policy_sha256']='b'*64
            if mutation=='cpu':row['cpu_percent_one_core'].pop('probe_children')
            if mutation=='counters':row['qdisc_samples']=[]
            if mutation=='dc':data['mock_dc']['completed']=143
            if mutation=='affinity':row['backend_thread_affinities']=[[1]]
            if mutation=='budget':data['resources_before']['memory_max']='999'
            if mutation=='nan':row['mbps']=float('nan')
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):analyze(data)

    def test_resource_capacity_and_probe_failures_suppress_parity(self):
        for kind,label in (('cpu','HARNESS_CPU_LIMITED'),('path','PATH_CONTROL_LIMITED'),('loss','PROBE_FAILURE')):
            data=fixture()
            for row in data['rows']:
                if kind=='cpu':row['cpu_percent_one_core']['probe_children']=80
                if kind=='loss':row['path_probe']['failures']=[{'type':'missing_reply'}]
            if kind=='path':
                for control in data['controls']:control['receiver_mbps']=80
            result=analyze(data)
            self.assertFalse(result['legacy_parity_all_cases'])
            self.assertTrue(all(c['classification']==label for c in result['comparisons']))
