"""Private three-node topology on one disposable VPS; no public listeners."""
import argparse
import concurrent.futures
import csv
import io
import json
import os
from pathlib import Path
import signal
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

from e2e_lab import FullLab
from lab import exact, send_payload, receive_payload
from mtproto_transport import mtproxy_connect
from network_lab import Network, NAMES, command, memory_available
from telemt_probe import SECRETS

parser=argparse.ArgumentParser()
parser.add_argument('--root',required=True)
parser.add_argument('--haproxy',default='haproxy')
parser.add_argument('--wg',default='wg')
parser.add_argument('--build',choices=('official','auto','bulk'),default='official')
parser.add_argument('--separate-dc',action='store_true')
profile=parser.add_mutually_exclusive_group()
profile.add_argument('--soak',action='store_true')
profile.add_argument('--benchmark',action='store_true')
profile.add_argument('--candidate-check',action='store_true')
parser.add_argument('--rtt',type=int,choices=(10,20,50,100,200),default=10)
parser.add_argument('role',choices=('run','backend','entry'))
parser.add_argument('--shared')
parser.add_argument('--index',type=int,choices=(1,2))
args=parser.parse_args()
if args.candidate_check and (args.build!='bulk' or not args.separate_dc or args.rtt not in (100,200)):
    parser.error('candidate path check requires bulk, separate mock DC and 100/200ms entry RTT')
if args.build=='bulk' and not args.candidate_check:
    parser.error('bulk is confined to the bounded candidate path check')
ROOT=Path(args.root).resolve()
HERE=Path(__file__).resolve()
if args.role!="run":
    expected="tdh-audit-a" if args.role=="entry" else "tdh-audit-b" if args.index==1 else "tdh-audit-b2"
    if subprocess.check_output(["ip","netns","identify"],text=True,timeout=3).strip()!=expected:
        parser.error("role must run in its dedicated private namespace")
signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))

def save(path,data):
    path=Path(path)
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data,indent=2)+'\n')
    temporary.replace(path)

class AgentHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(2)
        up=self.server.process.poll() is None and not self.server.down.exists()
        self.request.sendall(b'up\n' if up else b'down\n')

class Agent(socketserver.ThreadingTCPServer):
    daemon_threads=True
    allow_reuse_address=True

def backend():
    shared=Path(args.shared)
    address=f'10.77.{args.index}.2'
    binary=ROOT/'telemt-3.5.5/telemt'
    experimental=None
    if args.build!='official':
        metadata=json.loads((ROOT/f'{args.build}-build.json').read_text())
        binary=ROOT/f'telemt-{args.build}'
        experimental=(metadata['sha256'],metadata['version'])
    def create_lab():
        endpoint=(f'198.18.0.{1 if args.index==1 else 5}',26000) if args.separate_dc else None
        return FullLab(binary,listen_endpoint=(address,24431),start_haproxy=False,
                       experimental_binary=experimental,dc_endpoint=endpoint,artifact_registry=ROOT/'artifact-registry',
                       shared_binary_root=ROOT if args.candidate_check else None)
    lab=create_lab()
    agent=None
    client_sockets,observation_errors=[],[]
    last_observation=0
    try:
        agent=Agent((address,19101),AgentHandler)
        agent.process=lab.telemt
        agent.down=shared/f'backend{args.index}.down'
        threading.Thread(target=agent.serve_forever,daemon=True).start()
        while True:
            if (shared/f'backend{args.index}.stop').exists() and lab.telemt.poll() is None:
                lab.telemt.terminate()
                lab.telemt.wait(timeout=5)
            restart=shared/f'backend{args.index}.restart'
            if restart.exists():
                restart.unlink()
                lab.close()
                lab=create_lab()
                agent.process=lab.telemt
            state=(json.loads((shared/'dc.json').read_text())['backends'][args.index-1] if args.separate_dc else
                   {'completed':lab.dc.completed,'errors':lab.dc.errors})
            if args.candidate_check and not observation_errors and time.monotonic()-last_observation>=.5:
                from kernel_observer import observe_sockets
                try:
                    observed=observe_sockets(lab.telemt.pid,lab.listen)
                    client_sockets.extend({'rtt_ms':p.get('rtt_ms'),'rb':p.get('rb')}
                                          for p in observed if p['role']=='client')
                    client_sockets=client_sockets[-32:]
                except OSError as exc:
                    observation_errors.append(type(exc).__name__)
                last_observation=time.monotonic()
            save(shared/f'backend{args.index}.json',{'ready':lab.telemt.poll() is None,'completed':state['completed'],
                                                   'errors':state['errors'],'telemt':lab.version,
                                                   'client_sockets':client_sockets,'socket_observation_errors':observation_errors})
            time.sleep(.1)
    finally:
        if agent:
            agent.shutdown()
            agent.server_close()
        lab.close()

def network_extra(net):
    extra='tdh-audit-b2'
    assert extra not in {line.split()[0] for line in command('ip','netns','list').splitlines()}
    command('ip','netns','add',extra)
    net.created.append(extra)
    command('ip','-n',extra,'link','set','lo','up')
    command('ip','-n',NAMES[1],'link','add','tdh-lc0','type','veth','peer','name','tdh-lc1','netns',extra)
    for ns,dev,address in ((NAMES[1],'tdh-lc0','198.51.100.5'),(extra,'tdh-lc1','198.51.100.6')):
        command('ip','-n',ns,'address','add',address+'/30','dev',dev)
        command('ip','-n',ns,'link','set',dev,'up')
        command('ip','netns','exec',ns,'ethtool','-K',dev,'gro','off','gso','off','tso','off')
    command('ip','-n',extra,'route','add','default','via','198.51.100.5')
    command('ip','netns','exec',extra,'sysctl','-qw','net.ipv4.tcp_congestion_control=bbr')
    command('ip','netns','exec',extra,'tc','qdisc','replace','dev','tdh-lc1','root','fq')
    command('ip','netns','exec',NAMES[1],'tc','qdisc','replace','dev','tdh-lc0','root','netem','delay',f'{args.rtt/2:g}ms','rate','100mbit','limit','10000')
    wg=args.wg
    keys=[]
    for index in range(2):
        path=ROOT/f'dual-{index}.key'
        fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        net.key_paths.append(path)
        key=command(wg,'genkey')+'\n'
        with os.fdopen(fd,'w') as output: output.write(key)
        keys.append((path,command(wg,'pubkey',input=key)))
    for index,ns in enumerate((NAMES[0],extra)):
        dev='wg-lab2' if index==0 else 'wg-lab'
        command('ip','-n',ns,'link','add',dev,'type','wireguard')
        command('ip','-n',ns,'address','add',f'10.77.2.{index+1}/30','dev',dev)
        endpoint=['endpoint','198.51.100.6:51822','persistent-keepalive','25'] if index==0 else []
        command('ip','netns','exec',ns,wg,'set',dev,'listen-port','51822','private-key',str(keys[index][0]),
                'peer',keys[1-index][1],'allowed-ips',f'10.77.2.{2-index}/32',*endpoint)
        command('ip','-n',ns,'link','set',dev,'mtu','1420','up')
    for number, peers in ((1,((NAMES[0],'wg-lab'),(NAMES[2],'wg-lab'))),
                          (2,((NAMES[0],'wg-lab2'),(extra,'wg-lab')))):
        path=ROOT/f'dual-psk-{number}.key'
        fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        net.key_paths.append(path)
        with os.fdopen(fd,'w') as output: output.write(command(wg,'genpsk')+'\n')
        for ns,dev in peers:
            peer=command('ip','netns','exec',ns,wg,'show',dev,'peers')
            command('ip','netns','exec',ns,wg,'set',dev,'peer',peer,'preshared-key',str(path))
        command('ip','netns','exec',NAMES[0],'ping','-n','-c','3','-W','2',f'10.77.{number}.2',timeout=10)

def separate_dc(net,shared):
    """Attach mock DC links only to backend namespaces; no host/external route exists."""
    dc='tdh-audit-dc'
    command('ip','netns','add',dc)
    net.created.append(dc)
    command('ip','-n',dc,'link','set','lo','up')
    for index,backend_ns in enumerate((NAMES[2],'tdh-audit-b2')):
        back_dev,dc_dev=f'dc-back{index}',f'dc-link{index}'
        command('ip','-n',backend_ns,'link','add',back_dev,'type','veth','peer','name',dc_dev,'netns',dc)
        for ns,dev,last in ((backend_ns,back_dev,index*4+2),(dc,dc_dev,index*4+1)):
            command('ip','-n',ns,'address','add',f'198.18.0.{last}/30','dev',dev)
            command('ip','-n',ns,'link','set',dev,'up')
    process=subprocess.Popen(['ip','netns','exec',dc,'python3',str(HERE.with_name('mock_dc_worker.py')),
                              '--topology','dualhop','--output',str(shared/'dc.json')],
                             stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    net.processes.append(process)
    deadline=time.monotonic()+5
    while not (shared/'dc.json').exists():
        if process.poll() is not None or time.monotonic()>deadline:
            raise RuntimeError('separate mock DC readiness failed')
        time.sleep(.1)

def entry():
    shared=Path(args.shared)
    config=shared/'haproxy.cfg'
    cfg=f'''global
    description tdh-base-audit
    nbthread 1
    maxconn 1000
    maxpipes 128
    user nobody
    group nogroup
    stats socket {shared}/stats.sock mode 600 level admin
defaults
    mode tcp
    timeout connect 2s
    timeout client 30s
    timeout server 30s
    timeout check 3s
frontend mtp_tls
    bind 127.0.0.1:24433
    tcp-request inspect-delay 3s
    tcp-request content accept if {{ req.ssl_hello_type 1 }}
    use_backend telemt_a if {{ req.ssl_sni -i apple.com }}
    use_backend telemt_b if {{ req.ssl_sni -i gs.apple.com }}
'''
    for name,order in (('a',(1,2)),('b',(2,1))):
        cfg+=f'backend telemt_{name}\n'
        for position,number in enumerate(order):
            cfg+=(f'    server vps{number} 10.77.{number}.2:24431 check port 19101 inter 2s fall 3 rise 2 '
                  f'send-proxy-v2 agent-check agent-port 19101 agent-inter 2s'+(' backup' if position else '')+'\n')
    config.write_text(cfg)
    binary=args.haproxy
    subprocess.run([binary,'-c','-f',str(config)],check=True,capture_output=True,timeout=5)
    process=subprocess.Popen([binary,'-W','-db','-f',str(config)],cwd=shared,start_new_session=True,
                             stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    rows=[]
    reload_flow_active=[]
    def stat(cmd='show info'):
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(2)
            sock.connect(str(shared/'stats.sock'))
            sock.sendall((cmd+'\n').encode())
            data=bytearray()
            while block:=sock.recv(8192): data.extend(block)
            return data.decode()
    def counts():
        # DC completion can overlap a later transfer, particularly during reload.
        # Count assignments at connection establishment in every profile.
        records=list(csv.DictReader(io.StringIO(stat('show stat').lstrip('# '))))
        return [sum(int(r.get('stot') or 0) for r in records
                    if r['pxname'] in ('telemt_a','telemt_b') and r['svname']==f'vps{i}') for i in (1,2)]
    def transfer(domain,direction,size=131072,rate=2500000,fragment=False):
        if args.candidate_check and (not 0<size<=262144 or sum(r['bytes'] for r in rows)+size>2*1024**2):
            raise ValueError('candidate functional payload budget exceeded before transfer')
        phase="connect"
        try:
            started=time.monotonic()
            with socket.create_connection(('127.0.0.1',24433),timeout=8) as sock:
                sock.settimeout(8)
                phase="handshake"
                stream=mtproxy_connect(sock,domain,SECRETS[domain],fragment=fragment)
                phase="control"
                stream.sendall(__import__('struct').pack('!cQQ',direction.encode(),size,rate))
                phase="payload"
                digest=receive_payload(stream,size) if direction=='D' else send_payload(stream,size,rate)
                phase="digest"
                assert exact(stream,32)==digest,'SHA256 mismatch'
            return {'domain_index':list(SECRETS).index(domain)+1,'direction':direction,'bytes':size,
                    'elapsed_seconds':time.monotonic()-started,'sha256_verified':True}
        except Exception as exc:
            exc.lab_phase=phase
            raise
    def routed(domain,direction,expected,size=131072,rate=2500000,fragment=False):
        before=counts()
        result=transfer(domain,direction,size,rate,fragment)
        deadline=time.monotonic()+1
        while sum(counts())==sum(before) and time.monotonic()<deadline: time.sleep(.02)
        delta=[b-a for a,b in zip(before,counts())]
        assert delta[expected-1]==1 and sum(delta)==1,('unexpected route',delta,expected)
        result['backend_index']=expected
        rows.append(result)
    def wait_state(up):
        deadline=time.monotonic()+45
        while time.monotonic()<deadline:
            records=csv.DictReader(io.StringIO(stat('show stat').lstrip('# ')))
            state=next(r['status'] for r in records if r['pxname']=='telemt_a' and r['svname']=='vps1')
            if state.startswith('UP')==up: return
            time.sleep(.1)
        raise RuntimeError('agent state transition deadline')
    try:
        deadline=time.monotonic()+5
        while not (shared/'stats.sock').exists():
            assert process.poll() is None,'private HAProxy exited'
            assert time.monotonic()<deadline,'private HAProxy readiness deadline'
            time.sleep(.05)
        time.sleep(.4)
        if args.candidate_check:
            from candidate_contract import validate,validate_wireguard
            def wg_totals():
                totals={}
                for device in ('wg-lab','wg-lab2'):
                    records=[line.split() for line in command(args.wg,'show',device,'transfer').splitlines()]
                    if not records or any(len(row)!=3 for row in records):
                        raise RuntimeError('private WireGuard transfer counters unavailable')
                    totals[device]={'rx':sum(int(row[1]) for row in records),'tx':sum(int(row[2]) for row in records)}
                return totals
            before_wg=wg_totals()
            for domain,expected in (('apple.com',1),('gs.apple.com',2)):
                for direction in ('D','U'):
                    routed(domain,direction,expected,262144,131072,True)
            down=shared/'backend1.down'
            down.write_text('synthetic candidate agent gate')
            try:
                wait_state(False)
                for direction in ('D','U'):
                    routed('apple.com',direction,2,4096)
            finally:
                down.unlink(missing_ok=True)
            wait_state(True)
            routed('apple.com','U',1,4096)
            states=[json.loads((shared/f'backend{i}.json').read_text()) for i in (1,2)]
            observed=validate(rows,states)
            after_wg=wg_totals()
            wireguard={name:{key:after_wg[name][key]-before_wg[name][key] for key in ('rx','tx')} for name in before_wg}
            validate_wireguard(rows,wireguard)
            result={'classification':'SINGLE_VPS_CANDIDATE_PATH','scope':'actual HAProxy/WG+PSK/two candidate backends and separate synthetic DC; no public or production route',
                    'telemt':states[0]['telemt'],'rtt_ms_configured':args.rtt,'observed_client_tcp':observed,
                    'wireguard_psk':True,'wireguard_mtu':1420,'dual_sni_and_directions':True,
                    'wireguard_transfer_delta_bytes':wireguard,
                    'agent_failover_and_recovery':True,'source_core_staging':'private-shared-inode',
                    'payload_bytes':sum(r['bytes'] for r in rows),'rows':rows,
                    'backend_errors':[s['errors'] for s in states],
                    'existing_connection_migration_claimed':False,'throughput_or_production_capacity_claimed':False}
            save(ROOT/'results/candidate-path-bulk.json',result)
            print(json.dumps({k:v for k,v in result.items() if k!='rows'}),flush=True)
            return
        for domain,expected in (('apple.com',1),('gs.apple.com',2)):
            for direction in ('D','U'): routed(domain,direction,expected,fragment=True)
        if args.benchmark:
            for turn in range(3):
                for direction in (('D','U') if turn%2==0 else ('U','D')):
                    try:
                        routed('apple.com',direction,1,16777216,12500000)
                        rows[-1].update(benchmark_round=turn+1,complete=True)
                    except Exception as exc:
                        rows.append({'benchmark_round':turn+1,'direction':direction,'bytes':16777216,
                                     'complete':False,'exception':type(exc).__name__,
                                     'phase':getattr(exc,'lab_phase','unknown'),'sha256_verified':False})
                    progress={'scope':'single native VPS; real entry HAProxy, WG+PSK to two Telemt backends; local synthetic DC',
                              'telemt':json.loads((shared/'backend1.json').read_text())['telemt'],
                              'build':args.build,'rtt_ms_configured':args.rtt,'kernel':os.uname().release,
                              'rate_mbps_per_direction':100,'wireguard_psk':True,'wireguard_mtu':1420,'rows':rows}
                    save(ROOT/'results'/f'dualhop-native-{args.build}-benchmark-progress.json',progress)
            progress['backend_dc_errors']=[json.loads((shared/f'backend{i}.json').read_text())['errors'] for i in (1,2)]
            save(ROOT/'results'/f'dualhop-native-{args.build}-benchmark.json',progress)
            if args.build=='auto' and any(not r['complete'] for r in rows if 'benchmark_round' in r):
                raise RuntimeError('candidate WG benchmark failed')
            print(json.dumps({'build':args.build,'benchmark_groups':6,'complete':sum(r['complete'] for r in rows if 'benchmark_round' in r)}),flush=True)
            return
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            active=pool.submit(transfer,'apple.com','D',75000000 if args.soak else 8388608,250000 if args.soak else 1048576,True)
            time.sleep(.4)
            assert not active.done(),'long transfer completed before transition'
            (shared/'backend1.down').write_text('synthetic agent down')
            wait_state(False)
            routed('apple.com','D',2)
            cfg=cfg.replace('backend telemt_a\n','backend telemt_a\n    option splice-auto\n').replace('backend telemt_b\n','backend telemt_b\n    option splice-auto\n')
            cfg=cfg.replace('description tdh-base-audit','description tdh-auto-splice-audit')
            config.write_text(cfg)
            subprocess.run([binary,'-c','-f',str(config)],check=True,capture_output=True,timeout=5)
            reload_flow_active.append(not active.done())
            process.send_signal(signal.SIGUSR2)
            time.sleep(.5)
            wait_state(False)
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as sampling:
                bulk=sampling.submit(routed,'apple.com','D',2,8388608,10000000)
                pipes=0
                while not bulk.done():
                    info=dict(line.split(': ',1) for line in stat().splitlines() if ': ' in line)
                    pipes=max(pipes,int(info.get('PipesUsed',0))+int(info.get('PipesFree',0)))
                    time.sleep(.05)
                bulk.result(timeout=2)
            auto_pipes=pipes
            forced_pipes=None
            if not auto_pipes:
                cfg=cfg.replace('option splice-auto','option splice-request\n    option splice-response')
                cfg=cfg.replace('description tdh-auto-splice-audit','description tdh-forced-splice-test')
                config.write_text(cfg)
                subprocess.run([binary,'-c','-f',str(config)],check=True,capture_output=True,timeout=5)
                reload_flow_active.append(not active.done())
                process.send_signal(signal.SIGUSR2)
                time.sleep(.5)
                wait_state(False)
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as sampling:
                    bulk=sampling.submit(routed,'apple.com','D',2,8388608,10000000)
                    forced_pipes=0
                    while not bulk.done():
                        info=dict(line.split(': ',1) for line in stat().splitlines() if ': ' in line)
                        forced_pipes=max(forced_pipes,int(info.get('PipesUsed',0))+int(info.get('PipesFree',0)))
                        time.sleep(.05)
                    bulk.result(timeout=2)
                assert forced_pipes>0,('explicit splice did not activate', {k:v for k,v in info.items() if k in ('Description','PipesUsed','PipesFree','Maxpipes','Process_num')})
            (shared/'backend1.down').unlink()
            wait_state(True)
            routed('apple.com','U',1)
            result=active.result(timeout=320 if args.soak else 12)
            result['backend_index_at_connection_start']=1
            rows.append(result)
        if args.separate_dc:
            (shared/'backend1.stop').touch()
            wait_state(False)
            routed('apple.com','U',2)
            (shared/'backend1.stop').unlink()
            (shared/'backend1.restart').touch()
            wait_state(True)
            routed('apple.com','U',1)
            # Disconnect only the test entry's first tunnel, then verify recovery.
            command('ip','link','set','wg-lab','down')
            try:
                wait_state(False)
                routed('apple.com','U',2)
            finally:
                command('ip','link','set','wg-lab','up')
            wait_state(True)
            routed('apple.com','U',1)
        for number in (1,2):
            assert not json.loads((shared/f'backend{number}.json').read_text())['errors'],'mock DC failure'
        output={'scope':'single native VPS, private entry/two backends and router namespaces; actual HAProxy/WireGuard/Telemt, synthetic DC only',
                'kernel':os.uname().release,'wireguard_psk':True,'wireguard_mtu':1420,'rtt_ms_configured':10,
                'haproxy':subprocess.check_output([binary,'-v'],text=True,timeout=3).splitlines()[0],
                'telemt':json.loads((shared/'backend1.json').read_text())['telemt'],'agent_failover_and_recovery':True,'master_worker_reload_with_existing_transfer':all(reload_flow_active),'reload_flow_active':reload_flow_active,
                'splice_auto_pipes_allocated_observed':auto_pipes,'forced_splice_test_pipes_allocated_observed':forced_pipes,
                'master_worker_reload_count':1+(forced_pipes is not None),'rows':rows}
        output['classification']='SINGLE_VPS_VIRTUAL_3NODE'
        output['separate_mock_dc_namespace']=args.separate_dc
        output['actual_backend_process_restart_verified']=args.separate_dc
        output['wireguard_link_outage_recovery_verified']=args.separate_dc
        output['rtt_ms_configured']=args.rtt
        save(ROOT/'results'/f'dualhop-native-{args.build}{"-soak" if args.soak else "-benchmark" if args.benchmark else ""}.json',output)
        print(json.dumps({k:v for k,v in output.items() if k!='rows'}),flush=True)
    finally:
        if process.poll() is None: os.killpg(process.pid,signal.SIGTERM)
        try: process.wait(timeout=3)
        except subprocess.TimeoutExpired: os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=2)

def run():
    (ROOT/"results").mkdir(exist_ok=True,mode=0o700)
    net=Network(SimpleNamespace(root=str(ROOT),wg=args.wg,start_iperf=False,
                               rtt_ms=args.rtt,loss_percent=0,rate_mbps=100,mtu=1420))
    temp=tempfile.TemporaryDirectory(prefix='tdh-audit-dual-')
    shared=Path(temp.name)
    os.chown(shared,65534,65534)
    try:
        assert memory_available()>192*1024**2,'memory safety margin'
        net.setup()
        network_extra(net)
        if args.separate_dc:
            separate_dc(net,shared)
        for number,ns in ((1,NAMES[2]),(2,'tdh-audit-b2')):
            process=subprocess.Popen(['ip','netns','exec',ns,'python3',str(HERE),
                                      '--root',str(ROOT),'--haproxy',args.haproxy,'--wg',args.wg,'--build',args.build,'--rtt',str(args.rtt),
                                      *(['--separate-dc'] if args.separate_dc else []),
                                      *(['--candidate-check'] if args.candidate_check else []),
                                      'backend','--shared',str(shared),'--index',str(number)],
                                     stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            net.processes.append(process)
        deadline=time.monotonic()+45
        while not all((shared/f'backend{i}.json').exists() for i in (1,2)):
            for process in net.processes:
                if process.poll() is not None: raise RuntimeError('backend startup failed: '+process.stderr.read().decode()[:2000])
            assert time.monotonic()<deadline,'backend readiness deadline'
            time.sleep(.2)
        entry_args=['--candidate-check'] if args.candidate_check else ['--soak'] if args.soak else ['--benchmark'] if args.benchmark else []
        entry_deadline=90 if args.candidate_check else 450 if args.soak else 180 if args.benchmark else 150 if args.separate_dc else 60
        result=subprocess.run(['timeout','--signal=TERM','--kill-after=5',str(entry_deadline),'ip','netns','exec',NAMES[0],
                               'python3',str(HERE),'--root',str(ROOT),'--haproxy',args.haproxy,'--wg',args.wg,'--build',args.build,'--rtt',str(args.rtt),
                               *(['--separate-dc'] if args.separate_dc else []),
                               *entry_args,'entry','--shared',str(shared)],
                              capture_output=True,text=True,timeout=entry_deadline+10)
        if result.returncode: raise RuntimeError('entry experiment failed: '+result.stderr[:3000])
        print(result.stdout,flush=True)
    finally:
        net.close()
        for process in net.processes:
            if process.stderr: process.stderr.close()
        temp.cleanup()

if args.role=='run': run()
elif args.role=='backend': backend()
else: entry()
