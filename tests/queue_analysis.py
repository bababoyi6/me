"""Predeclared joint throughput/latency gates for four private counterfactuals."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import math

from long_path_analysis import number
from release_pins import NATIVE_BASES

VARIANTS=('baseline','lowat64','lowat256','wg-fq')
ORDERS=(('baseline','lowat64','wg-fq','lowat256'),('lowat64','lowat256','baseline','wg-fq'),
        ('lowat256','wg-fq','lowat64','baseline'),('wg-fq','baseline','lowat256','lowat64'))


def percentile(values,p):return sorted(values)[math.ceil(len(values)*p)-1]


def analyze(data):
    if (data.get('plan')!='queue' or data.get('accepted') is not True or data.get('complete') is not True or
            data.get('private_processes_closed') is not True or data.get('namespaces_removed') is not True or
            data.get('cleanup_errors')!=[] or data.get('oom_delta')!={'oom':0,'oom_kill':0}):
        raise ValueError('incomplete queue experiment')
    if data.get('payload_bytes_per_stream')!=32*1024**2 or data.get('rate_mbps')!=200:raise ValueError('workload drift')
    base=NATIVE_BASES.get(data.get('native_manifest',{}).get('upstream_commit'))
    if base is None:raise ValueError('unknown upstream identity')
    rows=data.get('rows',[])
    expected=[(t+1,v,n) for t,order in enumerate(ORDERS) for v in order for n in ((1,4) if t%2==0 else (4,1))]
    if [(r['round'],r['variant'],r['streams']) for r in rows]!=expected:raise ValueError('missing duplicate or reordered cohort')
    policy=set();index={}
    for row in rows:
        if row.get('direction')!='D' or row.get('rtt_ms')!=100 or row.get('version')!='telemt '+base+'+tdh.balanced1':raise ValueError('case identity drift')
        if row.get('completed')!=row['streams'] or row.get('errors')!=[]:raise ValueError('transfer failure')
        if not number(row.get('mbps')):raise ValueError('invalid throughput')
        cpu=row.get('cpu_percent_one_core',{})
        if any(not number(cpu.get(k),zero=True) for k in ('client','haproxy','controller','backend','dc')):raise ValueError('missing CPU')
        if not row.get('backend_thread_affinities') or any(a!=[data['cpu_assignment']['core']] for a in row['backend_thread_affinities']):raise ValueError('affinity drift')
        digest=row.get('config_policy_sha256','')
        if len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):raise ValueError('invalid config identity')
        policy.add(digest)
        tcp=[s.get('rtt_ms') for s in row.get('socket_samples',[]) if s.get('role')=='client']
        probe=row.get('path_probe',{})
        if not isinstance(probe.get('failures'),list):raise ValueError('missing probe loss observation')
        ping=[s.get('rtt_ms') for s in probe.get('samples',[])]
        if len(ping)<5 or not tcp or any(not number(v) for v in tcp+ping):raise ValueError('insufficient latency observations')
        index[row['round'],row['variant'],row['streams']]={**row,'tcp_rtt_median':statistics.median(tcp),
            'ping_p95':percentile(ping,.95),'probe_failed':bool(probe.get('failures',[]))}
    if len(policy)!=1:raise ValueError('configuration drift')
    controls=data.get('controls',[])
    if len(controls)!=2 or {c['direction'] for c in controls}!={'U','D'} or any(not number(c.get('receiver_mbps')) for c in controls):raise ValueError('missing TCP control')
    cases=[]
    for streams in (1,4):
        for variant in VARIANTS[1:]:
            old=[index[t,'baseline',streams] for t in range(1,5)]
            new=[index[t,variant,streams] for t in range(1,5)]
            throughput=[b['mbps']/a['mbps'] for a,b in zip(old,new)]
            tcp=[b['tcp_rtt_median']/a['tcp_rtt_median'] for a,b in zip(old,new)]
            ping=[b['ping_p95']/a['ping_p95'] for a,b in zip(old,new)]
            limited=any(r['cpu_percent_one_core']['client']+r['cpu_percent_one_core']['haproxy']+r['cpu_percent_one_core']['controller']>=70 or r['cpu_percent_one_core']['dc']>=70 for r in old+new)
            label=('HARNESS_CPU_LIMITED' if limited else 'PATH_CONTROL_LIMITED' if min(c['receiver_mbps'] for c in controls)<100 else
                'PROBE_FAILURE' if any(r['probe_failed'] for r in old+new) else
                'JOINT_GAIN' if min(throughput)>=.95 and max(tcp)<=.8 and max(ping)<=1.05 else 'NO_JOINT_GAIN')
            cases.append({'streams':streams,'candidate':variant,'classification':label,'throughput_ratios':throughput,
                'tcp_rtt_ratios':tcp,'path_ping_p95_ratios':ping,'baseline_median_mbps':statistics.median(r['mbps'] for r in old),
                'candidate_median_mbps':statistics.median(r['mbps'] for r in new),
                'baseline_tcp_rtt_ms':[r['tcp_rtt_median'] for r in old],'candidate_tcp_rtt_ms':[r['tcp_rtt_median'] for r in new],
                'baseline_ping_p95_ms':[r['ping_p95'] for r in old],'candidate_ping_p95_ms':[r['ping_p95'] for r in new]})
    decisions=[]
    for variant in VARIANTS[1:]:
        single=next(c for c in cases if c['streams']==1 and c['candidate']==variant)
        multi=next(c for c in cases if c['streams']==4 and c['candidate']==variant)
        eligible=(multi['classification']=='JOINT_GAIN' and single['classification'] in ('JOINT_GAIN','NO_JOINT_GAIN') and
            min(single['throughput_ratios'])>=.95 and max(single['tcp_rtt_ratios'])<=1.10 and max(single['path_ping_p95_ratios'])<=1.05)
        decisions.append({'candidate':variant,'decision':'REPLICATE_CANDIDATE' if eligible else 'DO_NOT_PROMOTE'})
    return {'source_commit':data['source_commit'],'transport_accepted':True,'cases':cases,'decisions':decisions,
        'scope':'one host, balanced four-way crossover; synthetic payload, no production or client claim',
        'rule':'every pair: throughput >=95%, median TCP RTT <=80%, path ping p95 <=105%; CPU/path/probe gates first'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('metrics');parser.add_argument('output');args=parser.parse_args()
    result=analyze(json.loads(Path(args.metrics).read_text()))
    result['analysis_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    Path(args.output).write_text(json.dumps(result,indent=2)+'\n')
