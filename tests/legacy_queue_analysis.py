"""Predeclared legacy parity and router-model checks; no production tuning claim."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics

from long_path_analysis import number

VARIANTS=('legacy-deep','native-deep','legacy-bounded','native-bounded')
ORDERS=(('legacy-deep','native-deep','native-bounded','legacy-bounded'),
        ('native-deep','legacy-bounded','legacy-deep','native-bounded'),
        ('legacy-bounded','native-bounded','native-deep','legacy-deep'),
        ('native-bounded','legacy-deep','legacy-bounded','native-deep'))
LIMITS={'deep':10000,'bounded':1500}
LEGACY_SHA='5dc5d9b5ac43a0f12a402159e127b3b69e18ccb355c99b0322e73d870d907100'
NATIVE_SHA='cd4c5ff500d956a8a7059e3e536ff139983a0c5897b4c410ea0e53c68a6cfa14'
UPSTREAM='66708d2866f3f3a4541c4fa67984d408e71a8af4'
BUILD_SOURCE='dec285fbdf5b42dd8be3e9e879bc0c2ad8994bc8'


def percentile(values,p):return sorted(values)[math.ceil(len(values)*p)-1]


def analyze(data):
    if (data.get('plan')!='legacy-queue' or data.get('accepted') is not True or data.get('complete') is not True or
        data.get('private_processes_closed') is not True or data.get('namespaces_removed') is not True or
        data.get('cleanup_errors')!=[] or data.get('oom_delta')!={'oom':0,'oom_kill':0}):raise ValueError('incomplete legacy comparison')
    if data.get('payload_bytes_per_stream')!=32*1024**2 or data.get('rate_mbps')!=200 or data.get('transfer_rate_per_stream')!=125000000:
        raise ValueError('workload drift')
    manifest=data.get('native_manifest',{})
    if (manifest.get('upstream_commit')!=UPSTREAM or manifest.get('manager_commit')!=BUILD_SOURCE or
        manifest.get('binaries',{}).get('balanced',{}).get('sha256')!=NATIVE_SHA or
        data.get('legacy_binary',{}).get('sha256')!=LEGACY_SHA):raise ValueError('unregistered binary identity')
    resources=data.get('resources_before',{});after=data.get('resources_after',{})
    if resources.get('cpu_max')!='300000 100000' or resources.get('memory_max')!=str(1536*1024**2) or resources.get('swap_max')!='0':
        raise ValueError('resource budget drift')
    if (not number(after.get('memory_peak')) or after['memory_peak']>1536*1024**2 or after.get('pids_current')!=1):raise ValueError('resource cleanup or peak invalid')
    rows=data.get('rows',[])
    expected=[(t+1,v,n) for t,order in enumerate(ORDERS) for v in order for n in ((1,4) if t%2==0 else (4,1))]
    if [(r.get('round'),r.get('variant'),r.get('streams')) for r in rows]!=expected:raise ValueError('missing duplicate or reordered cohort')
    index={};policies=set()
    for row in rows:
        variant=row['variant'];model=variant.split('-')[1]
        version='telemt 3.5.5+tdh.balanced2' if variant.startswith('legacy-') else 'telemt 3.5.17+tdh.balanced1'
        if row.get('version')!=version or row.get('rtt_ms')!=100 or row.get('direction')!='D' or row.get('queue_packet_limit')!=LIMITS[model]:
            raise ValueError('case identity or router-model drift')
        if row.get('router_packet_limits')!={'tdh-la1':LIMITS[model],'tdh-lb0':LIMITS[model]}:raise ValueError('actual router configuration drift')
        if row.get('completed')!=row['streams'] or row.get('errors')!=[] or not number(row.get('mbps')):raise ValueError('protocol or throughput failure')
        cpu=row.get('cpu_percent_one_core',{})
        if any(not number(cpu.get(k),zero=True) for k in ('client','haproxy','controller','probe_children','backend','dc')):raise ValueError('missing CPU observation')
        if not row.get('backend_thread_affinities') or any(a!=[data['cpu_assignment']['core']] for a in row['backend_thread_affinities']):raise ValueError('affinity drift')
        policy=row.get('config_policy_sha256','')
        if len(policy)!=64 or any(c not in '0123456789abcdef' for c in policy):raise ValueError('invalid configuration identity')
        policies.add(policy)
        probe=row.get('path_probe',{});ping=[s.get('rtt_ms') for s in probe.get('samples',[])]
        tcp=[s.get('rtt_ms') for s in row.get('socket_samples',[]) if s.get('role')=='client']
        qdisc=row.get('qdisc_samples',[])
        if (len(ping)<5 or not tcp or any(not number(v) for v in ping+tcp) or not isinstance(probe.get('failures'),list)):
            raise ValueError('missing latency or loss observations')
        if not qdisc or any(not any(q.get('kind')=='netem' and q.get('dev')=='tdh-la1' and
                all(number(q.get(k),zero=True) for k in ('backlog','drops','qlen')) for q in sample.get('rows',[])) for sample in qdisc):
            raise ValueError('missing bottleneck queue counters')
        index[row['round'],variant,row['streams']]={**row,'tcp_rtt':statistics.median(tcp),'ping_p95':percentile(ping,.95)}
    if len(policies)!=1:raise ValueError('configuration drift')
    controls=data.get('controls',[])
    if len(controls)!=4 or {(r.get('queue_packet_limit'),r.get('direction')) for r in controls}!={(n,d) for n in LIMITS.values() for d in ('U','D')}:
        raise ValueError('missing per-model TCP capacity control')
    if any(r.get('configured_rtt_ms')!=100 or not number(r.get('receiver_mbps')) for r in controls):raise ValueError('invalid path control')
    if data.get('mock_dc')!={'completed':144,'errors':[]}:raise ValueError('DC completion mismatch')
    comparisons=[]
    def compare(left,right,streams,kind):
        old=[index[t,left,streams] for t in range(1,5)];new=[index[t,right,streams] for t in range(1,5)]
        throughput=[b['mbps']/a['mbps'] for a,b in zip(old,new)]
        tcp=[b['tcp_rtt']/a['tcp_rtt'] for a,b in zip(old,new)]
        ping=[b['ping_p95']/a['ping_p95'] for a,b in zip(old,new)]
        cpu_limited=any(sum(r['cpu_percent_one_core'][k] for k in ('client','haproxy','controller','probe_children'))>=70 or r['cpu_percent_one_core']['dc']>=70 for r in old+new)
        models={LIMITS[v.split('-')[1]] for v in (left,right)}
        path_limited=min(r['receiver_mbps'] for r in controls if r['queue_packet_limit'] in models)<100
        bound=.8 if kind=='router-model' and streams==4 else 1.10
        good=min(throughput)>=.95 and max(tcp)<=bound and max(ping)<=bound
        label=('HARNESS_CPU_LIMITED' if cpu_limited else 'PATH_CONTROL_LIMITED' if path_limited else
               'PROBE_FAILURE' if any(r['path_probe']['failures'] for r in old+new) else
               ('LEGACY_PARITY' if good else 'LEGACY_PARITY_REVIEW') if kind=='legacy-parity' else
               ('MODEL_QUEUE_GAIN' if good else 'MODEL_QUEUE_REVIEW'))
        comparisons.append({'kind':kind,'baseline':left,'candidate':right,'streams':streams,'classification':label,
            'throughput_ratios':throughput,'tcp_rtt_ratios':tcp,'ping_p95_ratios':ping,
            'baseline_median_mbps':statistics.median(r['mbps'] for r in old),'candidate_median_mbps':statistics.median(r['mbps'] for r in new),
            'baseline_tcp_rtt_ms':[r['tcp_rtt'] for r in old],'candidate_tcp_rtt_ms':[r['tcp_rtt'] for r in new],
            'baseline_ping_p95_ms':[r['ping_p95'] for r in old],'candidate_ping_p95_ms':[r['ping_p95'] for r in new]})
    for n in (1,4):
        for model in LIMITS:compare('legacy-'+model,'native-'+model,n,'legacy-parity')
        for core in ('legacy','native'):compare(core+'-deep',core+'-bounded',n,'router-model')
    return {'source_commit':data['source_commit'],'transport_accepted':True,'payload_flows':80,'comparisons':comparisons,
        'legacy_parity_all_cases':all(c['classification']=='LEGACY_PARITY' for c in comparisons if c['kind']=='legacy-parity'),
        'scope':'one CI host; old and native exact binaries; router counterfactual is lab-only, not a production qdisc recommendation',
        'rules':'every paired cohort: native throughput>=95% old, TCP median and path ping p95<=110% old; CPU/path/loss gates first; four-flow model experiment additionally requires both latency measures<=80% deep'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('metrics');parser.add_argument('output');args=parser.parse_args()
    result=analyze(json.loads(Path(args.metrics).read_text()))
    result['analysis_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    Path(args.output).write_text(json.dumps(result,indent=2)+'\n')
