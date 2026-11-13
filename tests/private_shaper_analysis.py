"""Fail-closed single-variable screen; not a production release qualification."""
import argparse
import json
import math
from pathlib import Path
import re

from mixed_experience_analysis import trial, number
from legacy_queue_analysis import LEGACY_SHA, NATIVE_SHA
from path_capacity import validate as capacity_validate
from release_pins import TLS_CANDIDATES

TLS_CANDIDATE_SHA = '6e752224b2018b2f16ce1b0dd355a20f9d79c8e64741c870df28f1189c607673'


def verify_candidate_queue(report, rows):
    artifact, source_field, commit, version = TLS_CANDIDATES['new']
    manifest = report.get('candidate_manifest', {})
    pin = manifest.get('binaries', {}).get(artifact, {})
    if (manifest.get(source_field) != commit or not report.get('source_commit')
            or manifest.get('manager_commit') != report['source_commit']
            or manifest.get('architecture') != 'x86_64' or pin.get('version') != version
            or pin.get('sha256') != TLS_CANDIDATE_SHA):
        raise ValueError('new TLS candidate build identity drift')
    if report.get('namespaces_removed') is not True or report.get('private_processes_closed') is not True:
        raise ValueError('missing cleanup evidence')
    for row in rows:
        for key in ('wireguard_before', 'wireguard_after'):
            snapshots = row.get(key, {})
            if snapshots.keys() != {'tdh-audit-a', 'tdh-audit-b'}:
                raise ValueError('queue observations required on both private endpoints')
            for snapshot in snapshots.values():
                qdisc, classes = snapshot.get('qdisc', ''), snapshot.get('class', '')
                if row['queue_policy'] == 'original':
                    valid = qdisc.startswith('qdisc noqueue ') and not classes
                else:
                    valid = (qdisc.startswith('qdisc htb ')
                             and re.search(r'^qdisc fq_codel ', qdisc, re.M)
                             and re.search(r'\brate 180Mbit ceil 180Mbit\b', classes))
                if not valid:
                    raise ValueError('observed queue differs from the registered policy')


def analyze(report):
    candidate_queue = report.get('plan') == 'tls-candidate-shaper-v1'
    if (report.get('plan') not in ('private-shaper-screen-v1','managed-backend-screen-v1','tls-candidate-shaper-v1') or report.get('accepted') is not True or
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
    if any(not math.isclose(capacity_validate(c['raw']),c['mbps'],rel_tol=.001) for c in controls):
        raise ValueError('capacity raw evidence mismatch')
    budget=report.get('resources_before',{})
    if budget.get('cpu_max')!='300000 100000' or budget.get('memory_max')!=str(1536*1024**2) or budget.get('swap_max')!='0':
        raise ValueError('resource budget drift')
    rows=report.get('rows',[])
    rounds=2 if candidate_queue else 4
    expected=[(t+1,p) for t in range(rounds) for p in (('original','shaped180') if t%2==0 else ('shaped180','original'))]
    if [(r.get('round'),r.get('queue_policy')) for r in rows]!=expected:
        raise ValueError('registered AB/BA schedule incomplete')
    core=report.get('queue_core','balanced2')
    pins={'candidate':TLS_CANDIDATE_SHA} if candidate_queue else {'balanced2':LEGACY_SHA,'native17':NATIVE_SHA}
    if core not in pins or report.get('binary_sha256',{}).get(core)!=pins[core]:
        raise ValueError('selected core identity mismatch')
    if candidate_queue:
        verify_candidate_queue(report, rows)
    if any(r.get('variant')!=core or r.get('cohort')!=30 for r in rows):
        raise ValueError('workload or core drift')
    if any(not number(r.get('group_cpu_seconds')) or r['group_cpu_seconds']<=0 or
        not r.get('backend_thread_affinities') or any(a!=[report['cpu_assignment']['core']] for a in r['backend_thread_affinities']) for r in rows):
        raise ValueError('CPU measurement or affinity incomplete')
    if len({r['config_policy_sha256'] for r in rows})!=1:
        raise ValueError('configuration mismatch')
    expected_dc=sum(r['result']['dc_connections'] for r in rows)+rounds*4
    if report.get('mock_dc')!={'completed':expected_dc,'errors':[]}:
        raise ValueError('integrity or DC completion mismatch')
    payload_bytes=32*1024**2 if candidate_queue else 16*1024**2
    derived=[dict(round=r['round'],policy=r['queue_policy'],group_cpu_seconds=r['group_cpu_seconds'],**trial(r,payload_bytes)) for r in rows]
    pairs=[]
    for turn in range(1,rounds+1):
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
    result={'core':core,'pairs':pairs,'trials':derived,'all_pairs_joint_gain':all(p['joint_gain'] for p in pairs),
            'production_approval':False,'next_core_confirmation_permitted':all(p['joint_gain'] for p in pairs)}
    if candidate_queue:
        result.update(source_commit=report['source_commit'],binary_sha256=pins[core],plan=report['plan'])
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('input');parser.add_argument('output')
    parser.add_argument('--require-gain',action='store_true');args=parser.parse_args()
    result=analyze(json.loads(Path(args.input).read_text()))
    Path(args.output).write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    if args.require_gain and not result['all_pairs_joint_gain']:
        raise SystemExit('Queue experiment did not meet the joint gate; see the analysis report')
