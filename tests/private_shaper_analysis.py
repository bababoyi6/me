"""Fail-closed single-variable screen; not a production release qualification."""
import argparse
import json
from pathlib import Path

from mixed_experience_analysis import trial, number
from legacy_queue_analysis import LEGACY_SHA


def analyze(report):
    if (report.get('plan')!='private-shaper-screen-v1' or report.get('accepted') is not True or
        report.get('oom_delta')!={'oom':0,'oom_kill':0} or report.get('cleanup_errors')!=[] or
        report.get('resources_after',{}).get('pids_current')!=1 or
        report.get('binary_sha256',{}).get('balanced2')!=LEGACY_SHA):
        raise ValueError('incomplete execution, binary, resource or cleanup evidence')
    if tuple(report.get(k) for k in ('rtt_ms','rate_mbps','loss_percent'))!=(100,200,0):
        raise ValueError('network drift')
    controls=report.get('controls',[])
    if (sorted(c.get('direction') for c in controls)!=['D','U'] or
        any(c.get('receiver_local') is not True or not number(c.get('mbps')) or c['mbps']<100 for c in controls)):
        raise ValueError('capacity evidence insufficient')
    rows=report.get('rows',[])
    expected=[(t+1,p) for t in range(4) for p in (('original','shaped180') if t%2==0 else ('shaped180','original'))]
    if [(r.get('round'),r.get('queue_policy')) for r in rows]!=expected:
        raise ValueError('registered AB/BA schedule incomplete')
    if any(r.get('variant')!='balanced2' or r.get('cohort')!=30 for r in rows):
        raise ValueError('workload or core drift')
    if len({r['config_policy_sha256'] for r in rows})!=1:
        raise ValueError('configuration mismatch')
    expected_dc=sum(r['result']['dc_connections'] for r in rows)+4*4
    if report.get('mock_dc')!={'completed':expected_dc,'errors':[]}:
        raise ValueError('integrity or DC completion mismatch')
    derived=[dict(round=r['round'],policy=r['queue_policy'],group_cpu_seconds=r['group_cpu_seconds'],**trial(r)) for r in rows]
    pairs=[]
    for turn in range(1,5):
        old,new=(next(r for r in derived if r['round']==turn and r['policy']==p) for p in ('original','shaped180'))
        pair={'round':turn,'download_ratio':new['D_mbps']/old['D_mbps'],'upload_ratio':new['U_mbps']/old['U_mbps'],
              'message_p95_ratio':new['loaded_message_ms']['p95']/old['loaded_message_ms']['p95'],
              'new_connection_p95_ratio':new['loaded_new_connection_ms']['p95']/old['loaded_new_connection_ms']['p95'],
              'rss_ratio':new['peak_backend_rss']/old['peak_backend_rss'],
              'group_cpu_seconds_ratio':new['group_cpu_seconds']/old['group_cpu_seconds']}
        pair['eligible']=not any(r[k] for r in (old,new) for k in ('sample_limited','harness_limited'))
        pair['absolute_queue_budget_ok']=all(new['loaded_'+k+'_ms']['p95']<=new['baseline_'+k+'_ms']['p95']+100
            for k in ('message','new_connection'))
        pair['joint_gain']=(pair['eligible'] and pair['absolute_queue_budget_ok'] and
            min(pair['download_ratio'],pair['upload_ratio'])>=.95 and
            max(pair['message_p95_ratio'],pair['new_connection_p95_ratio'])<=.90 and
            pair['rss_ratio']<=1.10 and pair['group_cpu_seconds_ratio']<=1.10)
        pairs.append(pair)
    return {'pairs':pairs,'trials':derived,'all_pairs_joint_gain':all(p['joint_gain'] for p in pairs),
            'production_approval':False,'next_core_confirmation_permitted':all(p['joint_gain'] for p in pairs)}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('input');parser.add_argument('output');args=parser.parse_args()
    Path(args.output).write_text(json.dumps(analyze(json.loads(Path(args.input).read_text())),indent=2,allow_nan=False)+'\n')
