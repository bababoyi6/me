"""One preregistered fairness screen, not a production or capacity verdict."""
import argparse
import json
from pathlib import Path

from mixed_experience_analysis import trial
from legacy_queue_analysis import NATIVE_SHA, UPSTREAM


def analyze(data):
    if (data.get('plan')!='fair-quantum-screen-v1' or data.get('accepted') is not True or
            data.get('complete') is not True or data.get('oom_delta')!={'oom':0,'oom_kill':0} or
            data.get('namespaces_removed') is not True or data.get('private_processes_closed') is not True or
            data.get('cleanup_errors')!=[]):raise ValueError('incomplete experiment')
    manifest=data.get('fair_manifest',{})
    if (manifest.get('upstream_commit')!=UPSTREAM or manifest.get('reference_binary_sha256')!=NATIVE_SHA or
            manifest.get('manager_commit')!=data.get('source_commit') or manifest.get('quantum_bytes')!=65536 or
            manifest.get('version')!='telemt 3.5.17+tdh.fair1' or manifest.get('production_enabled') is not False):
        raise ValueError('candidate identity drift')
    if data.get('binary_sha256')!={'native17':NATIVE_SHA,'fair17':manifest.get('binary_sha256')}:
        raise ValueError('binary identity mismatch')
    budget=data.get('resources_before',{});after=data.get('resources_after',{})
    if (data.get('rtt_ms'),data.get('rate_mbps'),data.get('loss_percent'))!=(100,200,0):raise ValueError('network drift')
    if (budget.get('cpu_max'),budget.get('memory_max'),budget.get('swap_max'))!=('300000 100000',str(1536*1024**2),'0'):
        raise ValueError('budget drift')
    if after.get('pids_current')!=1 or not 0<after.get('memory_peak',0)<=1536*1024**2:raise ValueError('resource cleanup invalid')
    rows=data.get('rows',[])
    keys=[(t+1,v,30) for t in range(4) for v in (('native17','fair17') if t%2==0 else ('fair17','native17'))]
    if [(r.get('round'),r.get('variant'),r.get('cohort')) for r in rows]!=keys:raise ValueError('schedule drift')
    if len({r.get('config_policy_sha256') for r in rows})!=1:raise ValueError('config mismatch')
    if any(not r.get('backend_thread_affinities') or any(a!=[data['cpu_assignment']['core']] for a in r['backend_thread_affinities']) for r in rows):
        raise ValueError('single-core identity drift')
    if data.get('mock_dc')!={'completed':sum(r['result']['dc_connections'] for r in rows)+32,'errors':[]}:
        raise ValueError('DC integrity mismatch')
    controls=data.get('controls',[])
    if sorted(c.get('direction') for c in controls)!=['D','U']:raise ValueError('controls missing')
    control_limited=any(c['mbps']<100 for c in controls)
    values={(r['round'],r['variant']):trial(r) for r in rows}
    pairs=[]
    for t in range(1,5):
        old,new=values[t,'native17'],values[t,'fair17']
        pair={'round':t,'download_ratio':new['D_mbps']/old['D_mbps'],'upload_ratio':new['U_mbps']/old['U_mbps'],
            'message_p95_ratio':new['loaded_message_ms']['p95']/old['loaded_message_ms']['p95'],
            'new_connection_p95_ratio':new['loaded_new_connection_ms']['p95']/old['loaded_new_connection_ms']['p95'],
            'cpu_ratio':new['cpu']['backend']/old['cpu']['backend'] if old['cpu']['backend'] else None,
            'rss_ratio':new['peak_backend_rss']/old['peak_backend_rss']}
        pair['eligible']=(not control_limited and not any(v['sample_limited'] or v['harness_limited'] for v in (old,new)))
        pair['joint_gain']=(min(pair['download_ratio'],pair['upload_ratio'])>=.95 and pair['message_p95_ratio']<=.8 and
            pair['new_connection_p95_ratio']<=.9 and pair['cpu_ratio'] is not None and pair['cpu_ratio']<=1.10 and pair['rss_ratio']<=1.10)
        pairs.append(pair)
    return {'scope':'single 100ms/200Mbps/30 logical connections; four AB/BA pairs',
        'control_limited':control_limited,'pairs':pairs,'advance_to_qualification':all(p['eligible'] and p['joint_gain'] for p in pairs),
        'production_approved':False,'trials':[dict(round=t,variant=v,**value) for (t,v),value in values.items()]}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('input');parser.add_argument('output');a=parser.parse_args()
    result=analyze(json.loads(Path(a.input).read_text()))
    Path(a.output).write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
