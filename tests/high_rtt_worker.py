"""Reuse one private Telemt process for bounded, repeatable high-RTT trials."""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

from e2e_lab import FullLab, close_on_error
from network_lab import memory_available, cpu_snapshot, cpu_interval

parser=argparse.ArgumentParser()
parser.add_argument('--root',required=True)
parser.add_argument('--haproxy',default='haproxy')
parser.add_argument('--build',choices=('official','baseline','flush','auto'),required=True)
parser.add_argument('--output',required=True)
parser.add_argument('--rounds',type=int,choices=(1,2,3),default=3)
parser.add_argument('--streams',type=int,choices=(1,4,8,16,32))
parser.add_argument('--direction',choices=('D','U','both'),default='both')
parser.add_argument('--bytes',type=int,default=16777216)
parser.add_argument('--small-probes',action='store_true')
parser.add_argument('--slow-readers',action='store_true')
parser.add_argument('--rate-mbps',type=int,choices=(100,1000),default=100)
parser.add_argument('--dc-timeout',type=int,choices=(8,25),default=8)
parser.add_argument('--observe-network',action='store_true')
args=parser.parse_args()
if not 0<args.bytes<=256*1024**2 or args.bytes//(args.streams or 1)>128*1024**2:
    parser.error('payload must be bounded to 128 MiB per stream and 256 MiB total')
ROOT=Path(args.root).resolve()
if subprocess.check_output(["ip","netns","identify"],text=True,timeout=3).strip()!="tdh-audit-a":
    parser.error("worker must run in its private client namespace")
if os.geteuid()!=0 or not ROOT.name.startswith("tdh-audit-") or ROOT.stat().st_mode & 0o077 or ROOT.stat().st_uid!=0:
    parser.error("requires a root-owned private lab directory")
signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))

binary=ROOT/'telemt-3.5.5/telemt'
experimental=None
if args.build!='official':
    metadata=json.loads((ROOT/f'{args.build}-build.json').read_text())
    binary=ROOT/f'telemt-{args.build}'
    experimental=(metadata['sha256'],metadata['version'])
lab=FullLab(binary,args.haproxy,dc_endpoint=('198.51.100.2',26000),experimental_binary=experimental)
rows=[]
def ticks(pid=None):
    fields=Path(f'/proc/{pid or lab.telemt.pid}/stat').read_text().rsplit(')',1)[1].split()
    return (int(fields[11])+int(fields[12]))/os.sysconf('SC_CLK_TCK')
def oom():
    return next(int(line.split()[1]) for line in Path('/proc/vmstat').read_text().splitlines() if line.startswith('oom_kill '))

def rss():
    return next(int(line.split()[1])*1024 for line in Path(f'/proc/{lab.telemt.pid}/status').read_text().splitlines() if line.startswith('VmRSS:'))

def network_counters():
    lines=Path('/proc/net/snmp').read_text().splitlines()
    tcp={}
    for index,line in enumerate(lines[:-1]):
        if line.startswith('Tcp:'):
            all_values=dict(zip(line.split()[1:],map(int,lines[index+1].split()[1:])))
            tcp={key:all_values[key] for key in ('RetransSegs','OutSegs','InSegs')}
            break
    qdiscs=json.loads(subprocess.check_output(['ip','netns','exec','tdh-audit-wan','tc','-j','-s','qdisc','show'],text=True,timeout=3))
    endpoint_qdiscs={}
    for name in ('tdh-audit-a','tdh-audit-b'):
        values=json.loads(subprocess.check_output(['ip','netns','exec',name,'tc','-j','-s','qdisc','show'],text=True,timeout=3))
        endpoint_qdiscs[name]=[{key:row.get(key) for key in ('dev','kind','bytes','packets','drops','overlimits','requeues','backlog','qlen')}
                               for row in values if row['kind']=='fq']
    return {'client_namespace_tcp':tcp,'wan_netem':[
        {key:row.get(key) for key in ('dev','kind','bytes','packets','drops','overlimits','backlog','qlen')}
        for row in qdiscs if row['kind']=='netem'],'endpoint_fq':endpoint_qdiscs}
def observe():
    lines=subprocess.check_output(['ss','-Htinmp','state','established'],text=True,timeout=3).splitlines()
    values=[]
    for index,line in enumerate(lines):
        if f'pid={lab.telemt.pid},' not in line: continue
        role='client' if f'127.0.0.1:{lab.listen} ' in line else 'dc' if '198.51.100.2:26000 ' in line else None
        if not role: continue
        text=' '.join(lines[index+1:index+3])
        item={'role':role}
        for field,pattern in (('rb',r'\brb(\d+)'),('tb',r'\btb(\d+)'),('r_allocated',r'\br(\d+)'),('w_queued',r'\bw(\d+)'),
                              ('rtt_ms',r'\brtt:([\d.]+)'),('rwnd_limited_ms',r'\brwnd_limited:(\d+)ms'),
                              ('sndbuf_limited_ms',r'\bsndbuf_limited:(\d+)ms')):
            match=re.search(pattern,text)
            if match: item[field]=float(match[1])
        values.append(item)
    return values
def trial(turn,streams,direction):
    size=args.bytes//streams
    assert 0<size<=128*1024**2
    before_network=network_counters() if args.observe_network else None
    before_cpu=ticks()
    before_haproxy_cpu=ticks(lab.haproxy.pid)
    before_host_cpu=cpu_snapshot()
    started=time.monotonic()
    mem=memory_available()
    maximum_rss=rss()
    baseline_oom=oom()
    samples,probes,probe_errors=[],[],[]
    last_probe=-1
    with concurrent.futures.ThreadPoolExecutor(max_workers=streams) as pool, close_on_error(lab):
        futures=[pool.submit(lab.transfer,'apple.com',direction,size,args.rate_mbps*125000//streams,io_timeout=25,
                             direct=args.slow_readers, persistent=args.slow_readers,
                             read_pause=5 if args.slow_readers and direction=='D' else 0,
                             receive_buffer=32768 if args.slow_readers else 0) for _ in range(streams)]
        while not all(f.done() for f in futures):
            mem=min(mem,memory_available())
            maximum_rss=max(maximum_rss,rss())
            if mem<192*1024**2 or oom()!=baseline_oom: raise RuntimeError('memory safety stop')
            if time.monotonic()-started>65: raise RuntimeError('trial deadline')
            if args.small_probes and time.monotonic()-started-last_probe>=1:
                try:
                    probes.append(lab.transfer('gs.apple.com','D',1024,1250000,io_timeout=5))
                except Exception as exc:
                    probe_errors.append({'type':type(exc).__name__,'phase':getattr(exc,'lab_phase','unknown'),
                                         'received_bytes':getattr(exc,'lab_received_bytes',None)})
                last_probe=time.monotonic()-started
            samples.extend(observe())
            time.sleep(.2)
        success,errors,details=[],[],[]
        for future in futures:
            try: success.append(future.result(timeout=1))
            except Exception as exc:
                errors.append(type(exc).__name__)
                details.append({'type':type(exc).__name__,'phase':getattr(exc,'lab_phase','unknown'),
                                'received_bytes':getattr(exc,'lab_received_bytes',None)})
    elapsed=max((r['elapsed_seconds'] for r in success),default=time.monotonic()-started)
    row={'round':turn+1,'build':args.build,'telemt':lab.version,'streams':streams,'direction':direction,
         'link_rate_mbps':args.rate_mbps,'mock_dc_socket_timeout_seconds':args.dc_timeout,
         'direct_to_telemt':args.slow_readers,'client_receive_buffer_requested_bytes':32768 if args.slow_readers else None,
         'client_read_pause_seconds':5 if args.slow_readers and direction=='D' else 0,
         'expected_payload_bytes':size*streams,'sha256_verified_streams':len(success),'failed_streams':len(errors),
         'failure_types':errors,'complete':not errors and len(success)==streams,'elapsed_seconds':elapsed,
         'failure_details':details,'small_probe_errors':probe_errors,
         'mbps':size*streams*8/elapsed/1e6 if not errors else None,
         'telemt_cpu_seconds':ticks()-before_cpu,'haproxy_cpu_seconds':ticks(lab.haproxy.pid)-before_haproxy_cpu,
         'host_cpu_interval':cpu_interval(before_host_cpu,cpu_snapshot()),
         'minimum_available_memory_sampled_bytes':mem,'oom_delta':oom()-baseline_oom,
         'maximum_telemt_rss_sampled_bytes':maximum_rss,
         'small_probes':probes,'socket_samples':samples}
    if args.observe_network:
        row['network_counters_before']=before_network
        row['network_counters_after']=network_counters()
    rows.append(row)
    Path(args.output).write_text(json.dumps({'scope':'native private-namespace high-RTT mock-DC experiment; no Telegram files','rows':rows},indent=2)+'\n')
    print(json.dumps({k:v for k,v in row.items() if k not in ('socket_samples','small_probes')}),flush=True)
    if args.build in ('flush','auto') and (not row['complete'] or probe_errors): raise RuntimeError('candidate transfer failed')
try:
    stream_counts=(args.streams,) if args.streams else (1,4)
    for turn in range(args.rounds):
        cases=[(streams,direction) for streams in stream_counts for direction in (('D','U') if args.direction=='both' else (args.direction,))]
        for streams,direction in cases[::1 if turn%2==0 else -1]: trial(turn,streams,direction)
    lab.validate(0)
finally:
    lab.close()
