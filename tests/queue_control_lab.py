"""Real wg-quick/systemd lifecycle and interrupted queue transactions, private netns."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import queue_control as qc
from paired_performance import write, resources


def main(output):
    if (os.geteuid()!=0 or os.environ.get('GITHUB_ACTIONS')!='true' or
        os.readlink('/proc/self/ns/net')==os.readlink('/proc/1/ns/net')):raise RuntimeError('private disposable CI only')
    report={'source_commit':os.environ['TDH_SOURCE_COMMIT'],'accepted':False,'checks':{},'resources_before':resources()}
    temp=tempfile.TemporaryDirectory(prefix='tdh-queue-lab-');root=Path(temp.name);root.chmod(0o700)
    unit='tdh-queue-lab-'+uuid.uuid4().hex+'.service';unit_file=Path('/run/systemd/system')/unit
    dropin=unit_file.with_name(unit+'.d')/'90-telemt-queue.conf'
    manager=root/'manager';module=root/'queue_control.py'
    module.write_bytes((Path(__file__).resolve().parents[1]/'tools/queue_control.py').read_bytes());module.chmod(0o600)
    report['controller_sha256']=hashlib.sha256(module.read_bytes()).hexdigest()
    config=root/'tdh-b1.conf';config.write_text('[Interface]\nPrivateKey = '+qc.run('wg','genkey')+'\nAddress = 10.77.66.2/30\n');config.chmod(0o600)
    control=qc.QueueControl(root/'queue','tdh-b1',manager,dropin)
    manager.write_text('#!/usr/bin/python3\n# TDH_QUEUE_PROTOCOL=1\nimport sys\n'
        f'sys.path.insert(0,{str(root)!r})\nfrom queue_control import QueueControl\n'
        f'c=QueueControl({str(root/"queue")!r},"tdh-b1",{str(manager)!r},{str(dropin)!r})\n'
        'assert sys.argv[1]=="--queue-service" and sys.argv[2] in ("apply","remove")\n'
        'print(c.service(sys.argv[2]))\n');manager.chmod(0o755)
    baseline=hashlib.sha256(config.read_bytes()).hexdigest()
    stage='setup';write(output,report)
    try:
        if unit_file.exists() or unit_file.is_symlink():raise RuntimeError('refusing existing lab unit')
        unit_file.write_text(f'''[Unit]
Description=Owned private queue lifecycle lab
[Service]
Type=oneshot
RemainAfterExit=yes
NetworkNamespacePath=/proc/{os.getpid()}/ns/net
ExecStart=/usr/bin/wg-quick up {config}
ExecStop=/usr/bin/wg-quick down {config}
TimeoutStartSec=20
TimeoutStopSec=20
RuntimeMaxSec=180
CPUQuota=100%
MemoryMax=128M
MemorySwapMax=0
TasksMax=64
''');unit_file.chmod(0o644)
        qc.run('systemctl','daemon-reload');qc.run('systemctl','start',unit)
        report['initial_link']=control.kernel.link();report['initial_shape']=control.kernel.shape()
        report['checks']['default_off']=control.status()=={'enabled':False} and control.kernel.fresh()
        stage='enable_repeat'
        control.enable(180,200);first=control.journal.read_bytes()
        report['checks']['repeat_no_reset']=control.enable(180,200)=='unchanged' and control.journal.read_bytes()==first
        report['checks']['active']=control.status()['active']
        stage='drift'
        qc.run('tc','class','change','dev','tdh-b1','classid','7a00:10','htb','rate','181mbit','ceil','181mbit','quantum','1514')
        try:control.disable();raise AssertionError('drift was overwritten')
        except qc.QueueError:pass
        report['checks']['drift_preserved']=control.config.exists() and '181Mbit' in qc.run('tc','class','show','dev','tdh-b1')
        qc.run('tc','class','change','dev','tdh-b1','classid','7a00:10','htb','rate','180mbit','ceil','180mbit','quantum','1514')
        control.disable()
        stage='partial_failure'
        actual=qc.run
        def fail_leaf(*args):
            if args[:3]==('tc','qdisc','add') and 'fq_codel' in args:raise qc.QueueError('injected leaf failure')
            return actual(*args)
        with patch('queue_control.run',side_effect=fail_leaf):
            try:control.enable(180,200);raise AssertionError('fault not injected')
            except qc.QueueError:pass
        report['checks']['partial_failure_rolled_back']=(control.kernel.fresh() and
            control.kernel.link()['txqlen']==report['initial_link']['txqlen'] and not control.config.exists() and not dropin.exists())
        stage='crash_resume'
        child='''import os,signal,sys
sys.path.insert(0,sys.argv[1])
import queue_control as q
actual=q.run
def stop_after_root(*args):
    result=actual(*args)
    if args[:3]==('tc','qdisc','add') and 'htb' in args:os.kill(os.getpid(),signal.SIGKILL)
    return result
q.run=stop_after_root
c=q.QueueControl(sys.argv[2],'tdh-b1',sys.argv[3],sys.argv[4])
c.enable(180,200)
'''
        killed=subprocess.run(['python3','-c',child,str(root),str(root/'queue'),str(manager),str(dropin)],capture_output=True,timeout=20)
        report['checks']['crash_injected']=killed.returncode==-signal.SIGKILL and json.loads(control.journal.read_bytes())['phase']=='applying'
        report['checks']['crash_resumed']=control.enable(180,200)=='applied' and control.status()['active']
        stage='wg_restart'
        before=control.kernel.link()['ifindex'];qc.run('systemctl','restart',unit)
        report['checks']['wg_recreated_and_policy_restored']=control.kernel.link()['ifindex']!=before and control.status()['active']
        stage='failed_hook'
        original=control.config.read_bytes();changed=control.desired();changed['rate_mbps']=179
        qc.durable(control.config,qc.encoded(changed));qc.run('systemctl','restart',unit)
        report['checks']['hook_failure_keeps_wg_up']=qc.run('systemctl','is-active',unit)=='active' and control.kernel.fresh()
        qc.durable(control.config,original);control.service('apply')
        stage='hook_timeout'
        original_manager=manager.read_bytes()
        stalled=original_manager.replace(b'print(c.service(sys.argv[2]))',
            b'import time\nif sys.argv[2]=="apply":time.sleep(60)\nprint(c.service(sys.argv[2]))')
        qc.durable(manager,stalled,0o755)
        started=time.monotonic();subprocess.run(['systemctl','restart',unit],check=True,capture_output=True,timeout=20)
        elapsed=time.monotonic()-started
        report['hook_timeout_seconds']=elapsed
        report['checks']['hung_hook_keeps_wg_up']=(12<=elapsed<18 and qc.run('systemctl','is-active',unit)=='active' and control.kernel.fresh())
        qc.durable(manager,original_manager,0o755);control.service('apply')
        stage='disable_crash_resume'
        interrupted_off=child.replace("args[:3]==('tc','qdisc','add') and 'htb' in args",
            "args[:3]==('tc','qdisc','del')").replace('c.enable(180,200)','c.disable()')
        killed=subprocess.run(['python3','-c',interrupted_off,str(root),str(root/'queue'),str(manager),str(dropin)],capture_output=True,timeout=20)
        report['checks']['disable_crash_injected']=(killed.returncode==-signal.SIGKILL and
            json.loads(control.journal.read_bytes())['phase']=='removing' and control.kernel.fresh())
        control.disable()
        report['checks']['disable_crash_resumed']=not control.config.exists() and control.kernel.fresh()
        control.enable(180,200)
        stage='hook_drift'
        qc.durable(dropin,control.hook()+b'# operator change\n',0o644)
        try:control.disable();raise AssertionError('hook drift overwritten')
        except qc.QueueError:pass
        report['checks']['hook_drift_preserved']=dropin.read_bytes().endswith(b'# operator change\n')
        qc.durable(dropin,control.hook(),0o644)
        stage='pending_disable'
        def fail_reload(*args):
            if args==('systemctl','daemon-reload'):raise qc.QueueError('injected reload failure')
            return actual(*args)
        with patch('queue_control.run',side_effect=fail_reload):
            try:control.disable();raise AssertionError('reload fault not injected')
            except qc.QueueError:pass
        report['checks']['failed_disable_cannot_reenable']=control.service('apply')=='removal-pending' and control.kernel.fresh()
        control.disable();control.disable()
        report['checks']['disable_idempotent']=control.status()=={'enabled':False} and control.kernel.fresh() and not dropin.exists()
        stage='foreign_queue'
        qc.run('ip','link','set','tdh-b1','txqueuelen','1000');qc.run('tc','qdisc','add','dev','tdh-b1','root','fq')
        try:control.enable(180,200);raise AssertionError('foreign queue adopted')
        except qc.QueueError:pass
        report['checks']['foreign_queue_preserved']='qdisc fq ' in qc.run('tc','qdisc','show','dev','tdh-b1') and not control.config.exists()
        qc.run('tc','qdisc','del','dev','tdh-b1','root');qc.run('ip','link','set','tdh-b1','txqueuelen','0')
        report['checks']['wg_key_bytes_unchanged']=hashlib.sha256(config.read_bytes()).hexdigest()==baseline and stat_mode(config)==0o600
        report['checks']['state_private']=all(stat_mode(p)==0o600 for p in control.directory.iterdir() if p.is_file())
        report['accepted']=all(report['checks'].values())
    except BaseException as exc:
        report['failure']={'stage':stage,'type':type(exc).__name__,'message':str(exc) if isinstance(exc,qc.QueueError) else ''}
        raise
    finally:
        errors=[]
        try:qc.run('systemctl','stop',unit)
        except Exception:errors.append('unit-stop')
        if dropin.exists():dropin.unlink()
        if dropin.parent.exists():dropin.parent.rmdir()
        unit_file.unlink(missing_ok=True)
        try:qc.run('systemctl','daemon-reload')
        except Exception:errors.append('reload')
        subprocess.run(['systemctl','reset-failed',unit],capture_output=True,timeout=10)
        temp.cleanup();report['private_keys_removed']=not root.exists();report['cleanup_errors']=errors
        report['resources_after']=resources()
        report['oom_delta']={key:report['resources_after']['memory_events'][key]-report['resources_before']['memory_events'][key] for key in ('oom','oom_kill')}
        report['accepted']=(report['accepted'] and not errors and report['private_keys_removed'] and
            not any(report['oom_delta'].values()) and report['resources_after']['pids_current']==1)
        write(output,report)
    return 0 if report['accepted'] else 1


def stat_mode(path):return path.stat().st_mode & 0o777


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True,type=Path);args=parser.parse_args()
    signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
    raise SystemExit(main(args.output))
