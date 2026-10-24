import json
from pathlib import Path
import sys

root=Path('/root/tdh-audit-20261021/tdh-audit-causal-20261023')
sys.path.insert(0,str(root/'project/tools'))
from analyze_socket_policy import summarize
from analyze_upload_ab import summarize as upload_summary
report={'batches':[],'supervisors':[]}
for path in sorted((root/'results').glob('ab-*.json')):
    data=json.loads(path.read_text())
    if data.get('classification') not in ('SINGLE_VPS_SOCKET_POLICY_CAUSAL','SINGLE_VPS_SYNTHETIC'):
        continue
    trial_details=[]
    for entry in data['rows']:
        for row in entry.get('trial',{}).get('rows',[]):
            item={k:row.get(k) for k in ('build','socket_policy','mbps','streams','expected_payload_bytes',
                    'complete','measurement_complete','telemetry_errors','small_probe_errors','host_cpu_interval')}
            item['pair_round']=entry['pair_round']
            item['socket_roles']={}
            for role in ('client','dc'):
                samples=[r for r in row.get('socket_samples',[]) if r['role']==role]
                item['socket_roles'][role]={k:max((r[k] for r in samples if k in r),default=None)
                    for k in ('rb','tb','r_allocated','w_allocated','w_queued','rwnd_limited_ms','sndbuf_limited_ms')}
            item['observer_seconds_max']=max(row.get('socket_observer_batch_seconds',[]),default=None)
            trial_details.append(item)
    report['batches'].append({'file':path.name,'payload_mib':data['payload_bytes_total_per_trial']//1024**2,
        'rate_mbps':data['rate_mbps_per_direction'],'classification':data['classification'],
        'summary':summarize(data) if data['classification']=='SINGLE_VPS_SOCKET_POLICY_CAUSAL' else upload_summary(data),
        'trials':trial_details})
for folder in sorted(root.glob('run-tdh-audit-*')):
    data=json.loads((folder/'supervisor.json').read_text())
    manifest=json.loads((folder/'manifest.json').read_text())
    samples=data['samples']
    item={'run':folder.name,'budget':manifest['preflight']['budget'],
          **{k:data.get(k) for k in ('exit','stop_reason','cleanup_verified','host_oom_kill_delta')},
          'max_memory_sampled':max((r.get('memory.current') or 0 for r in samples),default=None),
          'max_memory_high_events':max((r.get('memory.events',{}).get('high',0) for r in samples),default=0),
          'max_memory_stat':{k:max((r.get('memory.stat',{}).get(k,0) for r in samples),default=0)
                             for k in ('anon','file','sock','slab','kernel_stack','pagetables','pgscan','pgsteal')}}
    report['supervisors'].append(item)
(root/'results/causal-summary.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
