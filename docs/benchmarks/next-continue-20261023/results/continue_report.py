"""Summarize all current remote trials, including failed/incomplete pairs."""
import hashlib
import json
from pathlib import Path
import statistics
import sys

root=Path('/root/tdh-audit-20261021/tdh-audit-continue-20261023')
sys.path.insert(0,str(root/'project/tools'))
from analyze_upload_ab import summarize
report={'classification':'SINGLE_VPS_SYNTHETIC','batches':[],'supervisors':[]}
for path in sorted((root/'results').glob('ab-*.json')):
    data=json.loads(path.read_text())
    if 'classification' not in data:
        continue
    batch={'file':path.name,'summary':summarize(data),'payload_mib':data['payload_bytes_total_per_trial']//1024**2,
           'rate_mbps':data['rate_mbps_per_direction'],'rounds':data['rounds'],'trials':[]}
    for entry in data['rows']:
        for row in entry.get('trial',{}).get('rows',[]):
            trial={key:row.get(key) for key in ('build','streams','complete','measurement_complete','mbps',
                    'telemetry_errors','failed_streams','failure_details','small_probe_errors','host_cpu_interval',
                    'maximum_telemt_rss_sampled_bytes','telemt_cpu_seconds','expected_payload_bytes',
                    'observer_implementation','sha256_verified_streams')}
            trial['pair_round']=entry['pair_round']
            durations=row.get('socket_observer_batch_seconds',[])
            trial['observer_seconds_total']=sum(durations)
            trial['observer_seconds_max']=max(durations,default=None)
            trial['observer_batches']=len(durations)
            trial['roles']={}
            for role in ('client','dc'):
                samples=[s for s in row.get('socket_samples',[]) if s['role']==role]
                fields={}
                for key in ('rb','tb','r_allocated','w_allocated','w_queued','rtt_ms','total_retrans',
                            'rwnd_limited_ms','sndbuf_limited_ms','busy_ms','notsent_bytes'):
                    values=[s[key] for s in samples if key in s]
                    fields[key]={'min':min(values),'max':max(values),'median':statistics.median(values)} if values else None
                trial['roles'][role]={'sample_rows':len(samples),'fields':fields}
            before,after=row.get('network_counters_before'),row.get('network_counters_after')
            if before and after:
                trial['namespace_tcp_delta']={k:after['client_namespace_tcp'][k]-v for k,v in before['client_namespace_tcp'].items()}
                trial['wan_netem_drops_delta']=sum(r['drops'] for r in after['wan_netem'])-sum(r['drops'] for r in before['wan_netem'])
            batch['trials'].append(trial)
    report['batches'].append(batch)
for folder in sorted(root.glob('run-tdh-audit-*')):
    data=json.loads((folder/'supervisor.json').read_text())
    plan=json.loads((folder/'manifest.json').read_text())
    samples=data['samples']
    report['supervisors'].append({'run':folder.name,'profile':plan['profile'],'budget':plan['preflight']['budget'],
        **{k:data.get(k) for k in ('exit','stop_reason','cleanup_verified','host_oom_kill_delta')},
        'memory_current_sampled_max':max((s.get('memory.current') or 0 for s in samples),default=0),
        'memory_high_event_max':max((s.get('memory.events',{}).get('high',0) for s in samples),default=0)})
(root/'results/continued-summary.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'batches':[{'payload_mib':b['payload_mib'],'rate_mbps':b['rate_mbps'],'summary':b['summary']} for b in report['batches']],
                  'supervisors':report['supervisors']}))
