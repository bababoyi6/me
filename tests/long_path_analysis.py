"""Fail-closed exploratory long-path comparisons; no population or client claims."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
from release_pins import NATIVE_BASES, RELEASES

VARIANTS=('balanced2','stable','stable-auto')
KEYS={(r,t,v,n,d) for r in (0,100) for t in (1,2,3) for v in VARIANTS for n in (1,4) for d in ('U','D')}


def number(value, *, zero=False):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and (value>=0 if zero else value>0)


def analyze(data):
    if data.get('plan','official') not in ('official','native','equal-load'):raise ValueError('unknown experiment plan')
    equal=data.get('plan')=='equal-load'
    native=data.get('plan','official') in ('native','equal-load')
    directions=('D',) if equal else ('U','D')
    if equal and data.get('transfer_rate_per_stream')!=2500000:raise ValueError('equal offered-load drift')
    base=NATIVE_BASES.get(data.get('native_manifest',{}).get('upstream_commit')) if native else None
    if native and base is None:raise ValueError('unknown native upstream identity')
    official=data.get('official_release','3.5.16')
    if official not in RELEASES:raise ValueError('unknown official release')
    profiles=(100,) if native else (0,100)
    variants=('control','balanced') if native else VARIANTS
    rounds=(1,2,3,4) if native else (1,2,3)
    expected_keys={(r,t,v,n,d) for r in profiles for t in rounds for v in variants for n in (1,4) for d in directions}
    if (data.get('accepted') is not True or data.get('complete') is not True or
            data.get('namespaces_removed') is not True or data.get('private_processes_closed') is not True or
            data.get('cleanup_errors')!=[] or data.get('oom_delta')!={'oom':0,'oom_kill':0}):
        raise ValueError('incomplete transport or cleanup')
    if data.get('payload_bytes_per_stream')!=32*1024**2 or data.get('rate_mbps')!=200:
        raise ValueError('unregistered workload')
    rows=data.get('rows',[])
    keys=[(r['rtt_ms'],r['round'],r['variant'],r['streams'],r['direction']) for r in rows]
    if len(keys)!=len(expected_keys) or set(keys)!=expected_keys:raise ValueError('missing or duplicate cohort')
    index=dict(zip(keys,rows));policies=set()
    for row in rows:
        version=('telemt '+base+('+tdh.control1' if row['variant']=='control' else '+tdh.balanced1')) if native else ('telemt 3.5.5+tdh.balanced2' if row['variant']=='balanced2' else 'telemt '+official)
        if row.get('version')!=version or row.get('completed')!=row['streams'] or row.get('errors')!=[]:
            raise ValueError('identity or transfer failure')
        policy=row.get('config_policy_sha256')
        if not isinstance(policy,str) or len(policy)!=64 or any(c not in '0123456789abcdef' for c in policy):
            raise ValueError('invalid configuration identity')
        policies.add(policy)
        if not number(row.get('mbps')) or not number(row.get('wall_seconds')):
            raise ValueError('invalid transfer metric')
        cpu=row.get('cpu_percent_one_core',{})
        if any(not number(cpu.get(k),zero=True) for k in ('backend','client','dc','haproxy','controller')):
            raise ValueError('missing or invalid CPU measurement')
        expected=data.get('cpu_assignment',{}).get('core')
        affinities=row.get('backend_thread_affinities')
        if not affinities or any(a!=[expected] for a in affinities):raise ValueError('core affinity drift')
    if len(policies)!=1:raise ValueError('configuration drift')
    controls=data.get('controls',[])
    if len(controls)!=2*len(profiles) or {(r['configured_rtt_ms'],r['direction']) for r in controls}!={(r,d) for r in profiles for d in ('U','D')}:
        raise ValueError('missing TCP path control')
    if any(not number(r.get('receiver_mbps')) for r in controls):raise ValueError('invalid TCP path control')
    control_index={(r['configured_rtt_ms'],r['direction']):r['receiver_mbps'] for r in controls}
    comparisons=[]
    for rtt in profiles:
        for n in (1,4):
            for direction in directions:
                for left,right in ((('control','balanced'),) if native else (('balanced2','stable'),('stable','stable-auto'))):
                    old=[index[rtt,t,left,n,direction] for t in rounds]
                    new=[index[rtt,t,right,n,direction] for t in rounds]
                    ratios=[b['mbps']/a['mbps'] for a,b in zip(old,new)]
                    limited=any((v['cpu_percent_one_core']['client']+v['cpu_percent_one_core']['haproxy']+
                        v['cpu_percent_one_core']['controller']>=70 or v['cpu_percent_one_core']['dc']>=70) for v in old+new)
                    observed=[]
                    for row in old+new:
                        values=[s.get('rtt_ms') for s in row.get('socket_samples',[]) if s.get('role')=='client']
                        if not values or any(not number(v,zero=True) for v in values):
                            raise ValueError('missing client-side measured RTT')
                        observed.append(statistics.median(values))
                    rtt_valid=all(80<=v<=200 for v in observed) if rtt else all(v<20 for v in observed)
                    label=('HARNESS_CPU_LIMITED' if limited else 'PATH_CONTROL_LIMITED' if control_index[rtt,direction]<100 else
                           'RTT_NOT_ESTABLISHED' if not rtt_valid else
                           'REPEATABLE_FASTER' if min(ratios)>1.10 else 'REPEATABLE_SLOWER' if max(ratios)<.90 else 'NO_CLEAR_CHANGE')
                    equal_ping=None
                    if equal:
                        probe_cpu=[r['cpu_percent_one_core'].get('probe_children') for r in old+new]
                        if any(not number(v,zero=True) for v in probe_cpu):raise ValueError('missing probe CPU')
                        equal_ping=[]
                        for row in old+new:
                            probe=row.get('path_probe',{})
                            samples=[s.get('rtt_ms') for s in probe.get('samples',[])]
                            if len(samples)<5 or any(not number(v) for v in samples) or not isinstance(probe.get('failures'),list):raise ValueError('missing equal-load path probe')
                            equal_ping.append(sorted(samples)[math.ceil(.95*len(samples))-1])
                        capacity=any(r['cpu_percent_one_core']['client']+r['cpu_percent_one_core']['haproxy']+r['cpu_percent_one_core']['controller']+r['cpu_percent_one_core']['probe_children']>=70 or r['cpu_percent_one_core']['dc']>=70 for r in old+new)
                        label=('HARNESS_CPU_LIMITED' if capacity else 'PATH_CONTROL_LIMITED' if control_index[rtt,direction]<100 else
                            'PROBE_FAILURE' if any(r['path_probe']['failures'] for r in old+new) else
                            'EQUAL_LOAD_WITHIN_BOUNDS' if min(ratios)>=.95 and all(80<=v<=130 for v in observed) and max(equal_ping)<=140 else 'EQUAL_LOAD_REVIEW')
                    comparisons.append({'rtt_ms':rtt,'streams':n,'direction':direction,'baseline':left,'candidate':right,
                        'classification':label,'ratios':ratios,'median_ratio':statistics.median(ratios),
                        'baseline_median_mbps':statistics.median(v['mbps'] for v in old),
                        'candidate_median_mbps':statistics.median(v['mbps'] for v in new),
                        'measured_client_rtt_medians_ms':observed,'equal_load_path_ping_p95_ms':equal_ping})
    return {'source_commit':data['source_commit'],'transport_accepted':True,'measured_payload_flows':sum(r['completed'] for r in rows),
        'comparisons':comparisons,'scope':('mechanism check at 20Mbps per stream, not a maximum-throughput comparison or production rate cap' if equal else 'one host, four balanced AB/BA rounds; same-toolchain native candidates; synthetic DC only' if native else 'one host, three rotated rounds; synthetic DC and lab-only preload; no production recommendation'),
        'rule':'all paired ratios >1.10 for exploratory faster, all <0.90 for slower; CPU <70%, TCP control >=100Mbps, measured RTT required'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('metrics');parser.add_argument('output');args=parser.parse_args()
    result=analyze(json.loads(Path(args.metrics).read_text()))
    result['analysis_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    Path(args.output).write_text(json.dumps(result,indent=2)+'\n')
