"""Split-process offline WireGuard experiment; never a production mode."""
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

from e2e_lab import FullLab, MockDc, sanitize_startup_log
from network_lab import Network, NAMES, command
from kernel_observer import observe_sockets
from stable_acceptance import PINS, stats
from paired_performance import resources, write
from core_install import archive_payload

HERE=Path(__file__).resolve()
VARIANTS=('balanced2','stable','stable-auto')
PAYLOAD=32*1024**2


def worker(role,cpu):
    expected=NAMES[0] if role=='client' else NAMES[2]
    if (os.geteuid()!=0 or os.environ.get('GITHUB_ACTIONS')!='true' or
            os.stat('/proc/self/ns/net').st_ino!=os.stat('/run/netns/'+expected).st_ino):
        raise RuntimeError('worker requires its owned CI namespace')
    os.sched_setaffinity(0,{cpu})
    options=json.loads(sys.stdin.readline())
    lab=dc=None
    try:
        if role=='core':
            kind=options['kind']
            lab=FullLab(options['binary'],listen_endpoint=('10.77.1.2',24431),start_haproxy=False,
                dc_endpoint=('198.18.0.1',26000),offline_lab=True,
                experimental_binary=options.get('experimental_binary') or ((PINS[kind][1],PINS[kind][2]) if kind=='balanced2' else None),
                socket_library=options.get('library'),socket_mode=options.get('policy','pass'))
            policy=lab.telemt_config.read_text().replace(f'127.0.0.1:{lab.api}','127.0.0.1:$API')
            ready={'pid':lab.telemt.pid,'version':lab.version,'config_policy_sha256':hashlib.sha256(policy.encode()).hexdigest()}
        elif role=='dc':
            dc=MockDc('198.18.0.1',26000,socket_timeout=25)
            ready={'pid':os.getpid()}
        else:ready={'pid':os.getpid()}
        print(json.dumps(ready),flush=True)
        for line in sys.stdin:
            request=json.loads(line)
            if request['command']=='stop':break
            if role=='dc':
                with dc.error_lock:result={'completed':dc.completed,'errors':list(dc.errors)}
            elif role=='client':
                endpoint=SimpleNamespace(real_dc=False,listen=24431,front=24443)
                if request['command']=='warm':
                    result={'rows':[FullLab.transfer(endpoint,domain,direction,4096,125000000,persistent=True,io_timeout=25)
                        for domain in ('apple.com','gs.apple.com') for direction in ('U','D')],'errors':[]}
                else:
                    rows,errors=[],[]
                    with concurrent.futures.ThreadPoolExecutor(max_workers=request['streams']) as pool:
                        futures=[pool.submit(FullLab.transfer,endpoint,('apple.com','gs.apple.com')[i%2],
                            request['direction'],PAYLOAD,125000000,persistent=True,io_timeout=25)
                            for i in range(request['streams'])]
                        for future in futures:
                            try:rows.append(future.result())
                            except Exception as error:
                                errors.append({'type':type(error).__name__,'phase':getattr(error,'lab_phase','unknown'),
                                    'reason':sanitize_startup_log(str(error))[:256]})
                    result={'rows':rows,'errors':errors}
            elif role=='core' and request['command']=='observe':
                result=observe_sockets(lab.telemt.pid,24431,namespace=NAMES[2])
            else:raise RuntimeError('unexpected core request')
            print(json.dumps(result),flush=True)
    finally:
        if lab:lab.close()
        if dc:dc.shutdown();dc.server_close()


class Child:
    def __init__(self,role,namespace,cpu,options,children):
        self.process=subprocess.Popen(['ip','netns','exec',namespace,sys.executable,'-u',str(HERE),
            '--role',role,'--cpu',str(cpu)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,text=True,start_new_session=True)
        children.append(self)
        self.send(options)
        self.ready=self.receive(45)
    def send(self,value):
        self.process.stdin.write(json.dumps(value)+'\n');self.process.stdin.flush()
    def receive(self,timeout=10):
        if not select.select([self.process.stdout],[],[],timeout)[0]:
            raise RuntimeError('bounded worker response deadline')
        line=self.process.stdout.readline()
        if not line:raise RuntimeError('private worker exited')
        return json.loads(line)
    def call(self,value):
        self.send(value);return self.receive()
    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:self.process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                try:os.killpg(self.process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                self.process.wait(timeout=3)
        if not self.process.stdout.closed:self.process.stdout.close()


def shape(rtt):
    for dev in ('tdh-la1','tdh-lb0'):
        command('ip','netns','exec',NAMES[1],'tc','qdisc','replace','dev',dev,'root',
                'netem','delay',f'{rtt/2:g}ms','rate','200mbit','limit','10000')


def main(args):
    if os.geteuid()!=0 or os.environ.get('GITHUB_ACTIONS')!='true':
        raise RuntimeError('disposable GitHub CI only')
    before=resources()
    if (before['cpu_max']!='300000 100000' or before['memory_max']!=str(1536*1024**2) or before['swap_max']!='0'):
        raise RuntimeError('3 CPU / 1.5GiB / no-swap budget required')
    cpus=sorted(os.sched_getaffinity(0))
    if len(cpus)<3:raise RuntimeError('three available logical CPUs required')
    backend_cpu,frontend_cpu,dc_cpu=cpus[:3]
    os.sched_setaffinity(0,{frontend_cpu})
    output=Path(args.output)
    native=bool(args.native_directory)
    variants=('control','balanced') if native else VARIANTS
    profiles=(100,) if native else (0,100)
    report={'scope':'split-process real FakeTLS/HAProxy/WireGuard/native cores; synthetic DC only',
        'plan':'native' if native else 'official',
        'source_commit':os.environ['TDH_SOURCE_COMMIT'],'complete':False,'accepted':False,
        'payload_bytes_per_stream':PAYLOAD,'rate_mbps':200,'cpu_assignment':{'core':backend_cpu,'client_and_haproxy':frontend_cpu,'dc':dc_cpu},
        'cpu_model':next(s.split(':',1)[1].strip() for s in Path('/proc/cpuinfo').read_text().splitlines() if s.startswith('model name')),
        'resources_before':before,'controls':[],'rows':[]}
    children=[];net=None;front=None;expected=0
    write(output,report)
    try:
        with tempfile.TemporaryDirectory(prefix='tdh-audit-longpath-') as directory:
            root=Path(directory)
            net=Network(SimpleNamespace(root=str(root),wg='wg',iperf='iperf3',start_iperf=True,
                rtt_ms=0,loss_percent=0,rate_mbps=200,mtu=1420))
            try:
                net.setup()
                command('ip','-n',NAMES[2],'address','add','198.18.0.1/32','dev','lo')
                binaries={}
                native_manifest=None
                if native:
                    bundle=Path(args.native_directory)
                    native_manifest=json.loads((bundle/'manifest.json').read_text())
                    if native_manifest['upstream_commit']!='84e5b827a6619fb2e09ad3d09f253cdffe04bc2f':raise RuntimeError('native upstream mismatch')
                    report['native_manifest']=native_manifest
                    for kind in variants:
                        payload=(bundle/('telemt-'+kind)).read_bytes()
                        if not 0<len(payload)<=64*1024**2 or hashlib.sha256(payload).hexdigest()!=native_manifest['binaries'][kind]['sha256']:
                            raise RuntimeError('native binary mismatch')
                        path=root/kind;path.write_bytes(payload);path.chmod(0o755);binaries[kind]=str(path)
                else:
                    for kind,pins in PINS.items():
                        payload,_=archive_payload(getattr(args,kind+'_archive'),pins[0],pins[2].removeprefix('telemt '),pins[1],False)
                        path=root/kind;path.write_bytes(payload);path.chmod(0o755);binaries[kind]=str(path)
                library=root/'socket-policy.so'
                subprocess.run(['cc','-shared','-fPIC','-Wall','-Wextra','-Werror',
                    str(HERE.with_name('socket_policy_lab.c')),'-ldl','-o',str(library)],check=True,timeout=30,capture_output=True)
                report['lab_socket_library_sha256']=hashlib.sha256(library.read_bytes()).hexdigest()
                dc=Child('dc',NAMES[2],dc_cpu,{},children)
                client=Child('client',NAMES[0],frontend_cpu,{},children)
                config=root/'haproxy.cfg'
                config.write_text('''global
    nbthread 1
defaults
    mode tcp
    timeout connect 5s
    timeout client 30s
    timeout server 30s
frontend lab
    bind 127.0.0.1:24443
    default_backend telemt
backend telemt
    server private 10.77.1.2:24431 send-proxy-v2
''')
                front=subprocess.Popen(['ip','netns','exec',NAMES[0],'haproxy','-db','-f',str(config)],
                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
                time.sleep(.3)
                if front.poll() is not None:raise RuntimeError('private frontend startup failed')
                for rtt in profiles:
                    shape(rtt)
                    for reverse in (False,True):
                        argv=['ip','netns','exec',NAMES[0],'iperf3','-c','10.77.1.2','-t','8' if native else '4','-O','3' if native else '1',
                              '-b','200M','-P','1','-J','--connect-timeout','3000']
                        if reverse:argv.append('-R')
                        control=json.loads(command(*argv,timeout=20))
                        if control.get('error'):raise RuntimeError('TCP path control failed')
                        report['controls'].append({'configured_rtt_ms':rtt,'direction':'D' if reverse else 'U',
                            'seconds':8 if native else 4,'omit_seconds':3 if native else 1,
                            'receiver_mbps':control['end']['sum_received']['bits_per_second']/1e6})
                    for turn in range(4 if native else 3):
                        offset=turn%len(variants)
                        for variant in variants[offset:]+variants[:offset]:
                            kind=variant if native else 'balanced2' if variant=='balanced2' else 'stable'
                            options={'kind':kind,'binary':binaries[kind]}
                            if native:
                                pin=native_manifest['binaries'][kind]
                                options['experimental_binary']=[pin['sha256'],pin['version']]
                            if variant=='stable-auto':options.update(library=str(library),policy='auto')
                            backend=Child('core',NAMES[2],backend_cpu,options,children)
                            try:
                                warm=client.call({'command':'warm'})
                                if warm['errors']:raise RuntimeError('protocol warmup failed')
                                expected+=4
                                cases=[(n,d) for n in (1,4) for d in ('U','D')]
                                for streams,direction in cases[::1 if turn%2==0 else -1]:
                                    ids={'backend':backend.ready['pid'],'client':client.ready['pid'],'dc':dc.ready['pid'],'haproxy':front.pid}
                                    snapshots={k:stats(pid) for k,pid in ids.items()}
                                    parent_cpu=time.process_time();started=time.monotonic();samples=[];last=-1
                                    client.send({'command':'run','streams':streams,'direction':direction})
                                    while not select.select([client.process.stdout],[],[],.1)[0]:
                                        if time.monotonic()-started>90:raise RuntimeError('trial deadline')
                                        if time.monotonic()-started-last>=1:
                                            samples.extend(backend.call({'command':'observe'}))
                                            last=time.monotonic()-started
                                    result=client.receive()
                                    elapsed=time.monotonic()-started
                                    cpu={k:100*(stats(pid)['cpu_seconds']-snapshots[k]['cpu_seconds'])/elapsed for k,pid in ids.items()}
                                    cpu['controller']=100*(time.process_time()-parent_cpu)/elapsed
                                    affinities=[sorted(os.sched_getaffinity(int(t.name))) for t in Path(f"/proc/{backend.ready['pid']}/task").iterdir()]
                                    if not affinities or any(a!=[backend_cpu] for a in affinities):
                                        raise RuntimeError('backend thread escaped its one-core assignment')
                                    row={'rtt_ms':rtt,'round':turn+1,'variant':variant,'streams':streams,'direction':direction,
                                        'version':backend.ready['version'],'config_policy_sha256':backend.ready['config_policy_sha256'],
                                        'completed':len(result['rows']),'errors':result['errors'],'wall_seconds':elapsed,
                                        'mbps':sum(v['bytes'] for v in result['rows'])*8/elapsed/1e6,
                                        'cpu_percent_one_core':cpu,'socket_samples':samples,
                                        'backend_rss_after_bytes':stats(backend.ready['pid'])['rss_bytes'],'backend_thread_affinities':affinities,
                                        'harness_limited':cpu['client']+cpu['haproxy']+cpu['controller']>=70 or cpu['dc']>=70}
                                    report['rows'].append(row);expected+=len(result['rows'])
                                    write(output,report)
                                    if row['errors'] or row['completed']!=streams:raise RuntimeError('synthetic integrity/ACK failure')
                                deadline=time.monotonic()+3
                                while True:
                                    state=dc.call({'command':'stats'})
                                    if state['errors']:raise RuntimeError('mock DC protocol failure')
                                    if state['completed']==expected:break
                                    if time.monotonic()>deadline:raise RuntimeError('mock DC completion mismatch')
                                    time.sleep(.1)
                            finally:backend.close()
                report['mock_dc']=dc.call({'command':'stats'})
                report['complete']=True
            finally:
                report['cleanup_errors']=[]
                for child in reversed(children):
                    try:child.close()
                    except Exception as error:report['cleanup_errors'].append(type(error).__name__)
                try:
                    if front is not None and front.poll() is None:
                        front.terminate()
                        try:front.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            try:os.killpg(front.pid,signal.SIGKILL)
                            except ProcessLookupError:pass
                            front.wait(timeout=3)
                finally:
                    if net is not None:net.close()
                report['private_processes_closed']=all(c.process.poll() is not None for c in children) and (front is None or front.poll() is not None)
    finally:
        report['resources_after']=resources()
        report['oom_delta']={k:report['resources_after']['memory_events'][k]-before['memory_events'][k] for k in ('oom','oom_kill')}
        report['namespaces_removed']=not set(NAMES).intersection(line.split()[0] for line in command('ip','netns','list').splitlines())
        report['accepted']=(report['complete'] and not any(report['oom_delta'].values()) and report['namespaces_removed']
            and report.get('private_processes_closed') is True and not report.get('cleanup_errors',['incomplete']))
        write(output,report)
    return 0 if report['accepted'] else 1


if __name__=='__main__':
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role',choices=('core','dc','client'))
    parser.add_argument('--cpu',type=int)
    parser.add_argument('--stable-archive')
    parser.add_argument('--balanced2-archive')
    parser.add_argument('--output')
    parser.add_argument('--native-directory')
    args=parser.parse_args()
    if args.role:worker(args.role,args.cpu)
    else:raise SystemExit(main(args))
