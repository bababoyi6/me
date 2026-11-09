"""Predeclared fail-closed mixed latency checks; never a production approval."""
import argparse
import json
import math
from pathlib import Path
import statistics

from legacy_queue_analysis import NATIVE_SHA, LEGACY_SHA
from release_pins import RELEASES

VARIANTS=('balanced2','official17','native17')
PROFILES={'low':(0,200,0),'rtt100':(100,200,0),'rtt200':(200,200,0),'weak':(100,100,.1)}


def number(value):return type(value) in (int,float) and math.isfinite(value) and value>=0


def quantiles(values):
    if not values or any(not number(v) for v in values):raise ValueError('missing/nonfinite measurements')
    ordered=sorted(values)
    return {'n':len(ordered),'p50':statistics.median(ordered),
            'p95':ordered[math.ceil(.95*len(ordered))-1],'p99':ordered[math.ceil(.99*len(ordered))-1],
            'p99_population_inference':False,'max':max(ordered)}


def trial(row):
    r=row['result']
    if (r.get('complete') is not True or r.get('errors')!=[] or r.get('logical_clients')!=row['cohort'] or
            r.get('idle_connections')!=row['cohort']-11 or len(r.get('idle_recovery_ms',[]))!=row['cohort']-11):
        raise ValueError('incomplete mixed transfer')
    bulk=r.get('bulk',[])
    if len(bulk)!=5 or sorted(v.get('direction') for v in bulk)!=['D']*4+['U']:
        raise ValueError('bulk role drift')
    for v in bulk:
        if v.get('bytes')!=16*1024**2 or v.get('sha256_verified') is not True or v.get('persistent_dc') is not True:
            raise ValueError('integrity/payload/ACK not verified')
        for k in ('elapsed_seconds','start_offset_seconds','end_offset_seconds'):
            if not number(v.get(k)):raise ValueError('missing bulk timing')
        if v['elapsed_seconds']<=0 or v['end_offset_seconds']<=v['start_offset_seconds']:
            raise ValueError('invalid bulk timing')
    rates=[v['bytes']*8/v['elapsed_seconds']/1e6 for v in bulk if v['direction']=='D']
    data={'bulk_mbps':sum(v['bytes'] for v in bulk)*8/1e6/(max(v['end_offset_seconds'] for v in bulk)-min(v['start_offset_seconds'] for v in bulk)),
          'download_jain':sum(rates)**2/(len(rates)*sum(v*v for v in rates)),
          'bulk_completion_ms':quantiles([v['elapsed_seconds']*1000 for v in bulk]),
          'idle_recovery_ms':quantiles(r['idle_recovery_ms'])}
    # Full duplex aggregate can exceed one direction's shaped rate and can
    # conceal a slower upload. Require each direction independently as well.
    for direction in ('D','U'):
        flows=[v for v in bulk if v['direction']==direction]
        data[direction+'_mbps']=sum(v['bytes'] for v in flows)*8/1e6/(
            max(v['end_offset_seconds'] for v in flows)-min(v['start_offset_seconds'] for v in flows))
    for phase in ('baseline','loaded'):
        for name in ('message','new_connection'):
            samples=r.get(phase,{}).get(name,[])
            if phase=='baseline' and len(samples)!=20:raise ValueError('baseline count drift')
            if not samples:raise ValueError('missing application probes')
            for sample in samples:
                if not number(sample.get('elapsed_ms')) or not number(sample.get('active_bulk')) or not number(sample.get('offset_seconds')):
                    raise ValueError('invalid application sample')
                if name=='new_connection' and sample.get('sha256_verified') is not True:raise ValueError('unverified new connection')
            values=[s['elapsed_ms'] for s in samples if phase=='baseline' or s['active_bulk']>0]
            data[phase+'_'+name+'_ms']=quantiles(values)
    cpu=row.get('cpu_percent_one_core',{})
    if set(cpu)!=set(('backend','client','dc','haproxy','controller','observer_children')) or any(not number(v) for v in cpu.values()):
        raise ValueError('CPU accounting incomplete')
    if not row.get('process_cpu_intervals') or not row.get('socket_samples') or not row.get('qdisc_samples'):
        raise ValueError('missing pressure observations')
    if not number(row.get('sampled_backend_peak_rss')):raise ValueError('missing RSS')
    data['harness_limited']=(sum(cpu[k] for k in ('client','haproxy','controller','observer_children'))>=70 or cpu['dc']>=70 or
        any(sum(s['percent'][k] for k in ('client','haproxy'))>=70 or s['percent']['dc']>=70 for s in row['process_cpu_intervals']))
    data['sample_limited']=any(data['loaded_'+n+'_ms']['n']<20 for n in ('message','new_connection'))
    data['peak_backend_rss']=row['sampled_backend_peak_rss']
    data['cpu']=cpu
    data['max_router_backlog_bytes']=max(q['backlog'] for s in row['qdisc_samples'] for q in s['rows'] if q['kind']=='netem')
    return data


def analyze(report):
    if (report.get('plan')!='mixed-experience-v1' or report.get('accepted') is not True or report.get('complete') is not True or
            report.get('private_processes_closed') is not True or report.get('namespaces_removed') is not True or
            report.get('cleanup_errors')!=[] or report.get('oom_delta')!={'oom':0,'oom_kill':0}):raise ValueError('execution or cleanup incomplete')
    profile=report.get('profile')
    if profile not in PROFILES or tuple(report.get(k) for k in ('rtt_ms','rate_mbps','loss_percent'))!=PROFILES[profile]:
        raise ValueError('unregistered network conditions')
    if report.get('binary_sha256')!={'balanced2':LEGACY_SHA,'official17':RELEASES['3.5.17'][1],'native17':NATIVE_SHA}:
        raise ValueError('binary identity drift')
    budget=report.get('resources_before',{});after=report.get('resources_after',{})
    if budget.get('cpu_max')!='300000 100000' or budget.get('memory_max')!=str(1536*1024**2) or budget.get('swap_max')!='0':
        raise ValueError('budget drift')
    if after.get('pids_current')!=1 or not number(after.get('memory_peak')) or after['memory_peak']>1536*1024**2:
        raise ValueError('resource/cleanup evidence invalid')
    rows=report.get('rows',[])
    expected=[(t+1,v,n) for t in range(3) for v in VARIANTS[t:]+VARIANTS[:t] for n in ((30,50,100) if t%2==0 else (100,50,30))]
    if [(r.get('round'),r.get('variant'),r.get('cohort')) for r in rows]!=expected:raise ValueError('schedule or coverage drift')
    policies={row.get('config_policy_sha256') for row in rows}
    if len(policies)!=1 or not all(isinstance(p,str) and len(p)==64 for p in policies):raise ValueError('configuration mismatch')
    if any(not r.get('backend_thread_affinities') or any(a!=[report['cpu_assignment']['core']] for a in r['backend_thread_affinities']) for r in rows):
        raise ValueError('single core identity drift')
    expected_dc=sum(r['result']['dc_connections'] for r in rows)+9*4
    if report.get('mock_dc')!={'completed':expected_dc,'errors':[]}:raise ValueError('DC integrity/completion mismatch')
    controls=report.get('controls',[])
    if sorted(c.get('direction') for c in controls)!=['D','U'] or any(not number(c.get('mbps')) for c in controls):
        raise ValueError('missing TCP capacity controls')
    capacity_limited=any(c['mbps']<report['rate_mbps']*.5 for c in controls)
    derived={(r['round'],r['variant'],r['cohort']):trial(r) for r in rows}
    comparisons=[]
    for n in (30,50,100):
        for variant in VARIANTS[1:]:
            pairs=[]
            for turn in (1,2,3):
                old=derived[turn,'balanced2',n];new=derived[turn,variant,n]
                pair={'round':turn,'throughput_ratio':new['bulk_mbps']/old['bulk_mbps'],
                    'download_ratio':new['D_mbps']/old['D_mbps'],'upload_ratio':new['U_mbps']/old['U_mbps'],
                    'message_p95_ratio':new['loaded_message_ms']['p95']/old['loaded_message_ms']['p95'],
                    'new_connection_p95_ratio':new['loaded_new_connection_ms']['p95']/old['loaded_new_connection_ms']['p95'],
                    'rss_ratio':new['peak_backend_rss']/old['peak_backend_rss']}
                pair['absolute_queue_budget_ok']=all(new['loaded_'+k+'_ms']['p95']<=new['baseline_'+k+'_ms']['p95']+100 for k in ('message','new_connection'))
                pair['parity']=(min(pair['throughput_ratio'],pair['download_ratio'],pair['upload_ratio'])>=.95 and pair['message_p95_ratio']<=1.10 and
                    pair['new_connection_p95_ratio']<=1.10 and pair['rss_ratio']<=1.10 and pair['absolute_queue_budget_ok'])
                pair['qualified']=(not capacity_limited and not old['harness_limited'] and not new['harness_limited']
                    and not old['sample_limited'] and not new['sample_limited'])
                pairs.append(pair)
            comparisons.append({'cohort':n,'variant':variant,'pairs':pairs,
                'all_pairs_qualified_and_parity':all(p['qualified'] and p['parity'] for p in pairs)})
    return {'profile':profile,'protocol_passed':True,'capacity_limited':capacity_limited,
        'sample_limited_trials':sum(v['sample_limited'] for v in derived.values()),
        'harness_limited_trials':sum(v['harness_limited'] for v in derived.values()),'comparisons':comparisons,
        'trials':[dict(round=t,variant=v,cohort=n,**value) for (t,v,n),value in derived.items()],
        'performance_parity_all_cases':all(c['all_pairs_qualified_and_parity'] for c in comparisons),
        'production_approval':False,'p99_note':'finite-sample nearest rank only; no stable population tail claim'}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('input');parser.add_argument('output');args=parser.parse_args()
    result=analyze(json.loads(Path(args.input).read_text()))
    Path(args.output).write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
