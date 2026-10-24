#!/usr/bin/env python3
"""Compare only complete same-binary socket-policy experiments; retain failures."""
import argparse
import json
import math
from pathlib import Path
import statistics

MODES=('pass','auto','client-auto','dc-auto')


def summarize(data):
    modes=tuple(data.get('socket_policy_cases',()))
    if data.get('classification')!='SINGLE_VPS_SOCKET_POLICY_CAUSAL' or modes not in (MODES,('pass','client-auto'),('pass','dc-auto'),('pass','auto-lowat'),('pass','auto','auto-lowat')):
        raise ValueError('not a declared bounded socket-policy experiment')
    groups=[]
    for streams in sorted({record['streams'] for record in data['rows']}):
        records=[record for record in data['rows'] if record['streams']==streams]
        cases={}
        for mode in modes:
            chosen=[r for r in records if r['socket_policy']==mode]
            valid,probes,failed_probes,failed=[],[],0,0
            for record in chosen:
                rows=record.get('trial',{}).get('rows',[])
                row=rows[0] if len(rows)==1 else {}
                failed_probes+=len(row.get('small_probe_errors',[]))
                if record.get('exit')!=0 or not row.get('complete') or not row.get('measurement_complete'):
                    failed+=1
                    continue
                if (record['build']!='flush' or row.get('build')!='flush' or row.get('socket_policy')!=mode or
                        row['streams']!=streams or row['direction']!='U' or
                        row['expected_payload_bytes']!=data['payload_bytes_total_per_trial'] or
                        row['link_rate_mbps']!=data['rate_mbps_per_direction']):
                    raise ValueError('workload, binary or policy mismatch')
                value=row.get('mbps')
                if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
                    raise ValueError('invalid throughput')
                valid.append(value)
                probes.extend(p['elapsed_seconds']*1000 for p in row.get('small_probes',[]))
            cases[mode]={'valid_trials':len(valid),'failed_trials':failed,'failed_probes':failed_probes,
                         'median_mbps':statistics.median(valid) if valid else None,
                         'median_small_probe_ms':statistics.median(probes) if probes else None}
        complete=(data['complete'] and len(records)==data['rounds']*len(modes) and all(
            cases[mode]['valid_trials']==data['rounds'] and not cases[mode]['failed_trials'] and
            not cases[mode]['failed_probes'] and
            {r['pair_round'] for r in records if r['socket_policy']==mode}==set(range(1,data['rounds']+1))
            for mode in modes))
        baseline=cases['pass']['median_mbps']
        groups.append({'streams':streams,'complete_counterbalanced_cases':complete,'cases':cases,
                       'change_percent_vs_pass':{mode:100*(cases[mode]['median_mbps']/baseline-1)
                                                if complete and baseline else None for mode in modes}})
    return {'classification':data['classification'],'complete':data['complete'],'groups':groups,
            'interpretation':'Synthetic DC segment, one shared host; not a production capacity or Telegram file-speed result.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('result',type=Path)
    args=parser.parse_args()
    print(json.dumps(summarize(json.loads(args.result.read_text())),indent=2))
