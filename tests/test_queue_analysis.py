"""Counterexamples forbid latency wins from failed or capacity-limited runs."""
import copy
import unittest
from queue_analysis import analyze, ORDERS
from queue_probe import ping_value


def fixture():
    data={'plan':'queue','accepted':True,'complete':True,'private_processes_closed':True,'namespaces_removed':True,
        'cleanup_errors':[],'oom_delta':{'oom':0,'oom_kill':0},'payload_bytes_per_stream':32*1024**2,'rate_mbps':200,
        'native_manifest':{'upstream_commit':'66708d2866f3f3a4541c4fa67984d408e71a8af4'},
        'source_commit':'fixture','cpu_assignment':{'core':0},'rows':[],
        'controls':[{'direction':d,'receiver_mbps':176} for d in ('U','D')]}
    for turn,order in enumerate(ORDERS):
        for variant in order:
            for streams in ((1,4) if turn%2==0 else (4,1)):
                data['rows'].append({'round':turn+1,'variant':variant,'streams':streams,'direction':'D','rtt_ms':100,
                    'version':'telemt 3.5.17+tdh.balanced1','completed':streams,'errors':[],'mbps':150,
                    'config_policy_sha256':'a'*64,'cpu_percent_one_core':{k:5 for k in ('backend','dc','client','controller','haproxy')},
                    'backend_thread_affinities':[[0]],'socket_samples':[{'role':'client','rtt_ms':220 if variant=='baseline' else 150}],
                    'path_probe':{'failures':[],'samples':[{'rtt_ms':150} for _ in range(8)]}})
    return data


class QueueAnalysisTests(unittest.TestCase):
    def test_joint_gain_requires_all_four_pairs_and_balanced_order(self):
        result=analyze(fixture())
        self.assertEqual(len(result['cases']),6)
        self.assertTrue(all(c['classification']=='JOINT_GAIN' for c in result['cases']))
        for variant in ('lowat64','lowat256','wg-fq'):
            self.assertEqual(sum(o.index('baseline')<o.index(variant) for o in ORDERS),2)

    def test_throughput_loss_or_ping_tail_regression_defeats_tcp_only_gain(self):
        for field in ('speed','ping'):
            data=fixture()
            for row in data['rows']:
                if row['variant']!='baseline':
                    if field=='speed':row['mbps']=140
                    else:row['path_probe']['samples'][0]['rtt_ms']=170
            self.assertTrue(all(c['classification']=='NO_JOINT_GAIN' for c in analyze(data)['cases']))
            self.assertTrue(all(c['decision']=='DO_NOT_PROMOTE' for c in analyze(data)['decisions']))

    def test_partial_missing_metrics_wrong_order_and_unknown_upstream_fail_closed(self):
        base=fixture()
        for key,value in (('completed',0),('mbps',float('nan')),('cpu_percent_one_core',{}),
                ('path_probe',{'samples':[{'rtt_ms':100}]*8}),('socket_samples',[]),('backend_thread_affinities',[[1]])):
            data=copy.deepcopy(base);data['rows'][0][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):analyze(data)
        data=fixture();data['rows'][0],data['rows'][1]=data['rows'][1],data['rows'][0]
        with self.assertRaises(ValueError):analyze(data)
        data=fixture();data['native_manifest']['upstream_commit']='unknown'
        with self.assertRaises(ValueError):analyze(data)

    def test_load_saturation_and_probe_loss_suppress_wins(self):
        for kind,label in (('cpu','HARNESS_CPU_LIMITED'),('path','PATH_CONTROL_LIMITED'),('loss','PROBE_FAILURE')):
            data=fixture()
            if kind=='path':data['controls'][0]['receiver_mbps']=80
            for row in data['rows']:
                if kind=='cpu':row['cpu_percent_one_core']['dc']=80
                if kind=='loss':row['path_probe']['failures']=[{'type':'missing_reply'}]
            self.assertTrue(all(c['classification']==label for c in analyze(data)['cases']))

    def test_ping_parser_does_not_turn_missing_reply_into_zero_latency(self):
        self.assertEqual(ping_value('64 bytes from 10.77.1.2: icmp_seq=1 ttl=64 time=100.7 ms'),100.7)
        self.assertIsNone(ping_value('1 packets transmitted, 0 received, 100% packet loss'))
