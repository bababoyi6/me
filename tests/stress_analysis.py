"""Resource/protocol gate, deliberately no throughput ranking at generator saturation."""
import argparse
import hashlib
import json
from pathlib import Path
from release_pins import NATIVE_BASES
from long_path_analysis import number


def assess(data):
    if (data.get('plan')!='sustained' or data.get('accepted') is not True or data.get('complete') is not True or
        data.get('private_processes_closed') is not True or data.get('namespaces_removed') is not True or
        data.get('cleanup_errors')!=[] or data.get('oom_delta')!={'oom':0,'oom_kill':0}):raise ValueError('incomplete resource run')
    if data.get('payload_bytes_per_stream')!=4*1024**2 or data.get('rate_mbps')!=200:raise ValueError('workload drift')
    before=data['resources_before'];after=data['resources_after']
    if before['cpu_max']!='300000 100000' or before['memory_max']!=str(768*1024**2) or before['swap_max']!='0':raise ValueError('resource budget drift')
    if (type(after.get('memory_peak')) is not int or not 0<after['memory_peak']<=768*1024**2 or
        type(after.get('pids_current')) is not int or after['pids_current']!=1):raise ValueError('resource bound or cleanup failed')
    version=NATIVE_BASES.get(data.get('native_manifest',{}).get('upstream_commit'))
    if version is None:raise ValueError('unknown upstream')
    rows=data.get('rows',[])
    expected={(v,n,d) for v in ('control','balanced') for n in (30,50,100) for d in ('U','D')}
    keys=[(r['variant'],r['streams'],r['direction']) for r in rows]
    if len(keys)!=len(expected) or set(keys)!=expected:raise ValueError('missing or duplicate resource cohort')
    policies={r.get('config_policy_sha256') for r in rows}
    if len(policies)!=1 or any(not isinstance(p,str) or len(p)!=64 or any(c not in '0123456789abcdef' for c in p) for p in policies):raise ValueError('configuration drift')
    for row in rows:
        if (row['completed']!=row['streams'] or row['errors'] or row['rtt_ms']!=100 or
            row['version']!='telemt '+version+('+tdh.control1' if row['variant']=='control' else '+tdh.balanced1')):raise ValueError('identity or protocol failure')
        if not row['backend_thread_affinities'] or any(a!=[data['cpu_assignment']['core']] for a in row['backend_thread_affinities']):raise ValueError('core affinity drift')
        if 'completion_ms' in row and (not isinstance(row['completion_ms'],dict) or
            any(not number(row['completion_ms'].get(k)) for k in ('p50','p95'))):raise ValueError('invalid observed completion latency')
    idle=data.get('idle_checks',[])
    if len(idle)!=2 or {r['variant'] for r in idle}!={'control','balanced'} or any(r['after']['fds']>r['before']['fds']+2 for r in idle):raise ValueError('missing descriptor recovery')
    if data.get('mock_dc')!={'completed':728,'errors':[]}:raise ValueError('DC completion mismatch')
    return {'accepted':True,'source_commit':data['source_commit'],'payload_flows':sum(r['completed'] for r in rows),
        'flow_timing_available':all('completion_ms' in r for r in rows),
        'memory_peak':after['memory_peak'],'idle_checks':idle,'generator_limited_batches':sum(r['harness_limited'] for r in rows),
        'scope':'100ms real WG, 4MiB/flow, 30/50/100 concurrency; whole lab <=768MiB, backend one logical CPU; no core throughput ceiling or 24h claim'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('metrics');parser.add_argument('output');args=parser.parse_args()
    result=assess(json.loads(Path(args.metrics).read_text()))
    result['analysis_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    Path(args.output).write_text(json.dumps(result,indent=2)+'\n')
