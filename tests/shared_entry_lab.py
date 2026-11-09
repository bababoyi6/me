"""Owned dual-WG raw scheduler proof, independent of Telemt performance."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import time
from types import SimpleNamespace

from network_lab import Network,NAMES,command
from paired_performance import resources,write
from path_capacity import validate


def second_tunnel(net):
    """Reuse the maintained Network owner/cleanup; independent second keypair."""
    ns='tdh-audit-b2';net.add_namespace(ns)
    command('ip','-n',ns,'link','set','lo','up')
    command('ip','-n',NAMES[1],'link','add','tdh-lc0','type','veth','peer','name','tdh-lc1','netns',ns)
    for where,dev,ip in ((NAMES[1],'tdh-lc0','198.51.100.5'),(ns,'tdh-lc1','198.51.100.6')):
        command('ip','-n',where,'address','add',ip+'/30','dev',dev);command('ip','-n',where,'link','set',dev,'up')
        command('ip','netns','exec',where,'ethtool','-K',dev,'gro','off','gso','off','tso','off')
    command('ip','-n',ns,'route','add','default','via','198.51.100.5')
    command('ip','netns','exec',ns,'sysctl','-qw','net.ipv4.tcp_congestion_control=bbr')
    command('ip','netns','exec',NAMES[1],'tc','qdisc','add','dev','tdh-lc0','root','netem',
        'limit','10000','delay',f'{net.args.rtt_ms/2:g}ms','rate',f'{net.args.rate_mbps}mbit')
    keys=[]
    for index in range(2):
        path=net.root/f'shared-wg-{index}.key';key=command('wg','genkey')+'\n'
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);net.key_paths.append(path)
        with os.fdopen(fd,'w') as f:f.write(key)
        keys.append((path,command('wg','pubkey',input=key)))
    for index,where in enumerate((NAMES[0],ns)):
        dev='wg-lab2' if index==0 else 'wg-lab'
        command('ip','-n',where,'link','add',dev,'type','wireguard')
        command('ip','-n',where,'address','add',f'10.77.2.{index+1}/30','dev',dev)
        endpoint=('endpoint','198.51.100.6:51822','persistent-keepalive','25') if index==0 else ()
        command('ip','netns','exec',where,'wg','set',dev,'listen-port','51822','private-key',str(keys[index][0]),
            'peer',keys[1-index][1],'allowed-ips',f'10.77.2.{2-index}/32',*endpoint)
        command('ip','-n',where,'link','set',dev,'mtu','1420','up')
    command('ip','netns','exec',NAMES[0],'ping','-n','-c','2','-W','2','10.77.2.2',timeout=8)


class Worker:
    def __init__(self):
        self.process=subprocess.Popen(['ip','netns','exec',NAMES[0],'python3',str(Path(__file__).with_name('shared_entry_worker.py'))],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.read()
    def read(self):
        import select
        if not select.select([self.process.stdout],[],[],15)[0]:raise RuntimeError('shared entry worker deadline')
        line=self.process.stdout.readline()
        if not line:raise RuntimeError('shared entry worker failed; preserve diagnostics in private CI')
        return json.loads(line)
    def call(self,action):
        self.process.stdin.write(json.dumps({'action':action})+'\n');self.process.stdin.flush()
        return self.read()['result']
    def close(self):
        if self.process.poll() is None:
            self.process.stdin.write('{"action":"stop"}\n');self.process.stdin.flush()
            self.process.wait(timeout=15)
        if self.process.returncode:raise RuntimeError('shared entry cleanup failed')


def main(output):
    if os.geteuid()!=0 or os.environ.get('GITHUB_ACTIONS')!='true':raise RuntimeError('disposable CI only')
    before=resources();report={'source_commit':os.environ['TDH_SOURCE_COMMIT'],'accepted':False,'checks':{},'capacity':[]}
    net=None;worker=None
    try:
        with tempfile.TemporaryDirectory(prefix='tdh-audit-shared-') as name:
            net=Network(SimpleNamespace(root=name,wg='wg',iperf='iperf3',start_iperf=False,rtt_ms=0,rate_mbps=200,loss_percent=0,mtu=1420))
            net.setup();second_tunnel(net)
            worker=Worker()
            for address in ('10.77.1.1','10.77.2.1'):
                process=subprocess.Popen(['ip','netns','exec',NAMES[0],'iperf3','-s','-B',address,'-p','5201'],
                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);net.processes.append(process)
            time.sleep(.4)
            roots={dev:command('ip','netns','exec',NAMES[0],'tc','qdisc','show','dev',dev) for dev in ('wg-lab','wg-lab2','tdh-la0')}
            key_hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in net.key_paths}
            worker.call('enable');report['checks']['repeat_no_reset']=worker.call('enable')=='unchanged'
            def capacity(index):
                ns=NAMES[2] if index==1 else 'tdh-audit-b2'
                payload=json.loads(command('ip','netns','exec',ns,'iperf3','-c',f'10.77.{index}.1','-t','6','-O','2',
                    '-b','200M','-P','1','-J','-R','--connect-timeout','3000',timeout=20))
                return {'tunnel':index,'raw':payload,'mbps':validate(payload)}
            with ThreadPoolExecutor(max_workers=2) as pool:report['capacity']=list(pool.map(capacity,(1,2)))
            report['single_idle_peer']=capacity(1)
            total=sum(r['mbps'] for r in report['capacity'])
            report['checks']['shared_budget']=150<total<185 and all(r['mbps']>40 for r in report['capacity'])
            report['checks']['idle_budget_borrowed']=report['single_idle_peer']['mbps']>150
            report['active_queue']=worker.call('snapshot')
            # A failed backend/interface must not make the healthy WG depend on it.
            command('ip','-n','tdh-audit-b2','link','set','wg-lab','down')
            command('ip','netns','exec',NAMES[0],'ping','-n','-c','2','-W','2','10.77.1.2',timeout=8)
            report['checks']['other_peer_failure_isolated']=True
            worker.call('disable');worker.call('disable')
            report['checks']['withdrawal_restores_qdisc']=all(command('ip','netns','exec',NAMES[0],'tc','qdisc','show','dev',d)==v for d,v in roots.items())
            report['checks']['keys_unchanged']=all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==v for p,v in key_hashes.items())
            report['checks']['shared_device_removed']='tdh-ifb-lab' not in command('ip','-n',NAMES[0],'-j','link','show')
            worker.close();worker=None
            net.close();net=None
    finally:
        errors=[]
        if worker:
            try:worker.close()
            except Exception as e:errors.append(type(e).__name__)
        if net:
            try:net.close()
            except Exception as e:errors.append(type(e).__name__)
        report['cleanup_errors']=errors;after=resources();report['resources_after']=after
        report['oom_delta']={k:after['memory_events'][k]-before['memory_events'][k] for k in ('oom','oom_kill')}
        report['accepted']=len(report['checks'])==7 and all(report['checks'].values()) and not errors and not any(report['oom_delta'].values()) and after['pids_current']==1
        write(output,report)
    return 0 if report['accepted'] else 1


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
    raise SystemExit(main(a.output))
