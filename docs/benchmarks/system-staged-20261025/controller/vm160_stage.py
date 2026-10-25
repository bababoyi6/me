"""Bounded sequential KVM readiness/functional staging on the authorized test host."""
import hashlib
import http.server
import importlib.util
import json
import os
from pathlib import Path
import shutil
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid

BASE=Path('/root/tdh-audit-20261021')
OLD=BASE/'vm-lab'
ROOT=BASE/'tdh-audit-vm160'
EXPECTED_SUPPORT = {'vm_lab.py': '89c5320aad57aa843488ca3a425b1e9e3d17de14c04d09f27c5e3541057266dd', 'vm_ssh.py': '9eb0f0fe8ffe0346a0ca48ea93b2c0a3dc49fd3a6108a586097227af5e3c8bfe'}
for name,expected in EXPECTED_SUPPORT.items():
    assert hashlib.sha256((BASE/name).read_bytes()).hexdigest()==expected,'unreviewed support helper'
sys.path.insert(0,str(BASE))
import vm_lab
from vm_ssh import ssh, SSH, ports


def report(path,value):
    with path.open('x') as stream:stream.write(json.dumps(value,indent=2)+'\n')


def inventory():
    spec=importlib.util.spec_from_file_location('current_preflight',BASE/'vm160_preflight_module.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.inventory()


def prepare():
    os.umask(0o077)
    assert not ROOT.exists() and not ROOT.is_symlink()
    assert OLD.resolve()==OLD and OLD.stat().st_uid==0 and OLD.stat().st_mode & 0o077==0
    assert not any(vm_lab.active(name) for name in vm_lab.NODES)
    assert not vm_lab.CG.exists()
    assert (OLD/'key').stat().st_mode & 0o777==0o600
    assert shutil.disk_usage(OLD).free>3*1024**3
    ROOT.mkdir(mode=0o700);(ROOT/'results').mkdir(mode=0o700);(ROOT/'generic').mkdir(mode=0o700)
    (ROOT/'runtime').symlink_to(OLD/'runtime',target_is_directory=True)
    (ROOT/'seed').symlink_to(OLD/'seed',target_is_directory=True)
    parents={}
    for name in ('entry','backend1','backend2'):
        source=OLD/name/'disk.qcow2'
        assert source.resolve()==source and source.is_file() and source.stat().st_uid==0
        parents[name]={'size':source.stat().st_size,'mtime_ns':source.stat().st_mtime_ns,'inode':source.stat().st_ino}
        folder=ROOT/name;folder.mkdir(mode=0o700)
        subprocess.run([str(OLD/'runtime/usr/bin/qemu-img'),'create','-q','-f','qcow2','-F','qcow2',
                        '-b',str(source),str(folder/'disk.qcow2')],env=vm_lab.ENV,check=True,timeout=20)
        boot=json.loads((OLD/name/'boot-generic.json').read_text())
        for kind in ('kernel','initrd'):
            path=Path(boot[kind]);assert path.is_relative_to(OLD/'generic') and path.resolve()==path and path.is_file()
            target=ROOT/'generic'/(name+'-'+kind+'-'+path.name)
            if not target.exists():os.link(path,target)
            assert target.stat().st_ino==path.stat().st_ino
            boot[kind]=str(target)
        report(folder/'boot-generic.json',boot)
    report(ROOT/'parent-disks.json',parents)
    report(ROOT/'preflight.json',inventory())
    print(json.dumps({'prepared_owned_overlays':3,'original_disks_modified':False,'started_guests':0}))


def reset_unstarted():
    assert ROOT.resolve()==ROOT and not ROOT.is_symlink() and ROOT.stat().st_uid==0 and ROOT.stat().st_mode & 0o077==0
    allowed_files={'parent-disks.json','preflight.json'}
    allowed_dirs={'results','generic','entry','backend1','backend2'}
    for name in ('entry','backend1','backend2'):
        allowed_files.update((name+'/disk.qcow2',name+'/boot-generic.json'))
        boot=json.loads((OLD/name/'boot-generic.json').read_text())
        for kind in ('kernel','initrd'):
            filename=Path(boot[kind]).name
            allowed_files.update(('generic/'+filename,'generic/'+name+'-'+kind+'-'+filename))
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():continue
        try:assert str(ROOT).encode() not in (process/'cmdline').read_bytes(),'owned fixture still referenced by a process'
        except FileNotFoundError:pass
    for path in ROOT.rglob('*'):
        relative=path.relative_to(ROOT).as_posix()
        if path.is_symlink():assert relative in ('runtime','seed') and path.readlink()==OLD/relative
        elif path.is_dir():assert relative in allowed_dirs
        else:assert path.is_file() and relative in allowed_files
    shutil.rmtree(ROOT)
    print(json.dumps({'removed_only_unstarted_owned_overlays':True,'original_disks_modified':False}))


def configure():
    assert ROOT.resolve()==ROOT and ROOT.stat().st_uid==0 and ROOT.stat().st_mode & 0o077==0
    vm_lab.ROOT=ROOT
    vm_lab.NODES={name:(index,1024,port) for name,(index,_,port) in vm_lab.NODES.items()}


def reset_entry_overlay(case_id):
    configure()
    assert not any(vm_lab.active(n) for n in vm_lab.NODES)
    assert not list(ROOT.glob('*/ramfile'))
    parents=json.loads((ROOT/'parent-disks.json').read_text())
    for name,prior in parents.items():
        info=(OLD/name/'disk.qcow2').stat()
        assert (info.st_size,info.st_mtime_ns,info.st_ino)==(prior['size'],prior['mtime_ns'],prior['inode'])
    folder=ROOT/'entry'
    info=json.loads(subprocess.check_output([str(OLD/'runtime/usr/bin/qemu-img'),'info','--output=json',str(folder/'disk.qcow2')],env=vm_lab.ENV,text=True,timeout=10))
    assert info['backing-filename']==str(OLD/'entry/disk.qcow2')
    private=ROOT/'private'/('prior-entry-'+case_id);private.mkdir(mode=0o700,parents=True)
    for name in ('console.log','qemu.log','process.json','forced-stop.json'):
        path=folder/name
        if path.exists():assert path.is_file() and not path.is_symlink();path.rename(private/name)
    (folder/'qmp').unlink(missing_ok=True)
    disk=folder/'disk.qcow2';assert disk.is_file() and not disk.is_symlink();disk.unlink()
    subprocess.run([str(OLD/'runtime/usr/bin/qemu-img'),'create','-q','-f','qcow2','-F','qcow2',
                    '-b',str(OLD/'entry/disk.qcow2'),str(disk)],env=vm_lab.ENV,check=True,timeout=20)


def interactive(node,command,label,action):
    sys.path.insert(0,str(OLD/'runtime/usr/lib/python3/dist-packages'))
    import pexpect
    private=ROOT/'private';private.mkdir(mode=0o700,exist_ok=True)
    child=None
    try:
        with (private/(label+'.log')).open('x',encoding='utf-8') as log:
            child=pexpect.spawn(SSH[0],[*SSH[1:],'-tt','-p',str(ports[node]),'root@127.0.0.1',command],
                encoding='utf-8',codec_errors='replace',timeout=240)
            child.logfile_read=log
            action(child)
            child.expect(pexpect.EOF,timeout=20);child.close()
            assert child.exitstatus==0,'guest command failed; private transcript retained'
    finally:
        if child is not None and child.isalive():child.sendcontrol('c');child.close(force=True)


def install_entry(attestation,case_id):
    assert attestation['manager_sha256']=='2d1e6c59ef0ce69d0fd734d1d9a6cab89881ac92e26e54d38f4b2080bc16acc3'
    # Exact manager blob from repository commit 585f089, on an attested owned guest overlay.
    def uninstall(child):
        child.expect('输入 UNINSTALL');child.sendline('UNINSTALL');child.expect('卸载完成',timeout=90)
    interactive('entry','a uninstall','entry-owned-fixture-uninstall-'+case_id,uninstall)
    check=ssh('entry',"""python3 - <<'PY'
from pathlib import Path
p=Path('/var/lib/telemt-dual-hop')
if p.exists():
 assert p.is_dir() and not p.is_symlink();p.rmdir()
assert not Path('/etc/telemt-dual-hop').exists()
assert not Path('/usr/local/sbin/telemt-dual-hop').exists()
PY
""",35)
    assert check.returncode==0,'owned guest uninstall did not produce fresh project paths'
    readme=(BASE/'vm160_README.md').read_text(encoding='utf-8')
    command=readme.split('```bash\n',1)[1].split('\n```',1)[0]
    assert 'https://raw.githubusercontent.com/bababoyi6/me/main/telemt-dual-hop.sh' in command
    def initialize(child):
        child.expect('请选择');child.sendline('1')
        child.expect('请输入入口 VPS 公网 IPv4: ',timeout=240);child.sendline('11.254.254.1')
        child.expect('按 Enter 返回控制面板',timeout=120);child.sendline('')
        child.expect('请选择');child.sendline('0')
    interactive('entry','env PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin '+command,
                'entry-public-one-command-install-'+case_id,initialize)
    code="""python3 - <<'PY'
import hashlib,json,shlex
from pathlib import Path
p=Path('/usr/local/sbin/telemt-dual-hop')
state={}
for line in Path('/etc/telemt-dual-hop/state.env').read_text().splitlines():
 k,_,v=line.partition('=')
 if k in ('TDH_ROLE','INSTALLED_VERSION','B1_REGISTERED','B2_REGISTERED'):state[k]=shlex.split(v)[0]
files=[q for q in Path('/etc/telemt-dual-hop').rglob('*') if q.is_file() and not q.is_symlink()]
files.extend((p,Path('/usr/local/bin/a')))
snapshot={str(q):hashlib.sha256(q.read_bytes()).hexdigest() for q in files}
Path('/root/tdh-vm160-protected.json').write_text(json.dumps(snapshot))
print(json.dumps({'manager_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'state':state,
 'protected_file_count':len(snapshot),'snapshot_sha256':hashlib.sha256(json.dumps(snapshot,sort_keys=True).encode()).hexdigest()}))
PY
"""
    response=ssh('entry',code,35);assert response.returncode==0
    result=json.loads(response.stdout)
    assert result['manager_sha256']=='376f00d36a4fb1aa082002d96612d297d4f01e2e55935a1a3cf7155ed9f5d26f'
    assert result['state']['TDH_ROLE']=='entry' and result['state']['INSTALLED_VERSION']=='1.6.0'
    assert result['state']['B1_REGISTERED']=='0' and result['state']['B2_REGISTERED']=='0'
    import re
    transcript=(ROOT/'private'/('entry-public-one-command-install-'+case_id+'.log')).read_text()
    codes=re.findall(r'TDH4\.[A-Za-z0-9_-]+',transcript);assert len(codes)==2
    report(ROOT/'private/enrollment.json',dict(zip(('backend1','backend2'),codes)))
    result.update(actual_public_readme_command=True,download_boundary_substituted=False,
                  stage='entry initialization; full cluster services await peer enrollment')
    return result


def worker(node,phase='readiness',case_id='initial'):
    configure()
    group=Path('/proc/self/cgroup').read_text().strip().split('::')[-1]
    assert group.startswith('/system.slice/tdh-audit-vm160-') and group.endswith('.service')
    vm_lab.CG=Path('/sys/fs/cgroup')/group.lstrip('/')
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    seed=http.server.ThreadingHTTPServer(('127.0.0.1',22880),vm_lab.Seed)
    switch=vm_lab.Switch(('127.0.0.1',22881),vm_lab.Link)
    for server in (seed,switch):threading.Thread(target=server.serve_forever,daemon=True).start()
    result={'node':node,'operation':phase,'case_id':case_id,'complete':False,
            'logical_guest_memory_mib':1024,'capacity_claimed':False}
    started=time.monotonic()
    try:
        vm_lab.start(node)
        deadline=time.monotonic()+420
        code="""python3 - <<'PY'
import hashlib,json,os,socket,subprocess
from pathlib import Path
p=Path('/usr/local/sbin/telemt-dual-hop')
print(json.dumps({'root':os.geteuid()==0,'hostname':socket.gethostname(),
 'virt':subprocess.check_output(['systemd-detect-virt'],text=True).strip(),
 'pid1':Path('/proc/1/comm').read_text().strip(),
 'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
 'memory_total_kib':next(int(s.split()[1]) for s in Path('/proc/meminfo').read_text().splitlines() if s.startswith('MemTotal:')),
 'manager_sha256':hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() and not p.is_symlink() else None,
 'project_state_present':Path('/etc/telemt-dual-hop/state.env').is_file()}))
PY
"""
        probe_errors=[]
        while time.monotonic()<deadline:
            assert vm_lab.active(node),'owned guest stopped before readiness'
            try:
                response=ssh(node,code,25)
            except (OSError,subprocess.SubprocessError) as error:
                probe_errors.append(type(error).__name__)
                result['readiness_probe_errors']=probe_errors[-20:]
                time.sleep(2)
                continue
            if response.returncode==0:
                value=json.loads(response.stdout)
                assert value['root'] and value['hostname']=='tdh-test-'+node
                assert value['virt'] in ('kvm','qemu') and value['pid1']=='systemd'
                assert value['boot_id']!=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
                assert value['memory_total_kib']>=768*1024
                result['attestation']=value;result['ready_seconds']=time.monotonic()-started
                break
            time.sleep(2)
        else:raise RuntimeError('single-guest readiness deadline; do not increase budget or retry')
        if phase=='install':result['installation']=install_entry(result['attestation'],case_id)
        vm_lab.qmp(node,'system_powerdown')
        deadline=time.monotonic()+120
        while vm_lab.active(node) and time.monotonic()<deadline:time.sleep(.5)
        assert not vm_lab.active(node),'normal shutdown exceeded 120-second deadline'
        vm_lab.stop(node)
        assert not (ROOT/node/'forced-stop.json').exists(),'normal shutdown exceeded deadline'
        result['complete']=True
    except Exception as error:
        result['error_type']=type(error).__name__
        result['error_reason']=str(error)[:200]
        raise
    finally:
        try:vm_lab.stop(node)
        finally:
            seed.shutdown();switch.shutdown()
            seed.server_close();switch.server_close()
            result['elapsed_seconds']=time.monotonic()-started
            report(ROOT/'results'/(phase+'-'+node+'-'+case_id+'.json'),result)


def run(node,phase='readiness',case_id='initial'):
    configure()
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert not any(vm_lab.active(name) for name in vm_lab.NODES)
    suffix=node+'-'+phase+'-'+case_id
    assert not (ROOT/'results'/('run-'+suffix+'.json')).exists()
    before=inventory();limits=before['budget']
    assert limits['eligible'] and before['kernel_oom_kill_count'] is not None and not before['existing_lab_namespaces']
    assert not subprocess.check_output(['systemctl','list-units','--all','--plain','--no-legend','tdh-audit-*'],text=True,timeout=10).strip()
    assert shutil.disk_usage(ROOT).free>3*1024**3
    for port in (22041,22042,22043,22443,22880,22881):
        with socket.socket() as connection:
            connection.settimeout(.2);assert connection.connect_ex(('127.0.0.1',port))!=0
    unit='tdh-audit-vm160-'+uuid.uuid4().hex[:10]
    properties={'MemoryMax':limits['memory_max_bytes'],'MemoryHigh':limits['memory_high_bytes'],
        'MemorySwapMax':0,'CPUQuota':'60%','TasksMax':192,'RuntimeMaxSec':540,'TimeoutStopSec':20,
        'KillMode':'control-group','OOMPolicy':'stop','UMask':'0077','LimitFSIZE':2*1024**3,
        'WorkingDirectory':str(BASE),'IOWeight':10}
    command=['systemd-run','--quiet','--wait','--pipe','--service-type=exec','--unit='+unit,
             *['--property='+k+'='+str(v) for k,v in properties.items()],
             '--',sys.executable,'-B',str(Path(__file__).resolve()),'worker',node,phase,case_id]
    plan={'operation':phase,'case_id':case_id,'controller_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'node':node,'unit':unit,
          'preflight':before,'properties':properties,'disk_reserve_bytes':1024**3,'log_limit_bytes':8*1024**2}
    report(ROOT/'results'/('plan-'+suffix+'.json'),plan)
    samples=[];reason=None;group=None
    with (ROOT/'results'/('unit-'+suffix+'.log')).open('x') as log:
        process=subprocess.Popen(command,stdout=log,stderr=log)
        started=time.monotonic()
        try:
            while process.poll() is None:
                if group is None:
                    cg=subprocess.check_output(['systemctl','show',unit,'--property=ControlGroup','--value'],text=True,timeout=5).strip()
                    if cg:group=Path('/sys/fs/cgroup')/cg.lstrip('/')
                current=int((group/'memory.current').read_text()) if group and (group/'memory.current').exists() else 0
                events=dict(line.split() for line in (group/'memory.events').read_text().splitlines()) if group and (group/'memory.events').exists() else {}
                available=next(int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
                host_oom=next(int(line.split()[1]) for line in Path('/proc/vmstat').read_text().splitlines() if line.startswith('oom_kill '))
                samples.append({'elapsed_seconds':time.monotonic()-started,'memory_current':current,'memory_events':events,
                                'host_available':available})
                logs=sum(p.stat().st_size for p in ROOT.rglob('*.log') if p.is_file() and not p.is_symlink())
                if host_oom!=before['kernel_oom_kill_count']:reason='host_oom_changed'
                elif available<limits['host_reserve_bytes']:reason='host_memory_reserve'
                elif int(events.get('oom',0))>0:reason='cgroup_oom'
                elif shutil.disk_usage(ROOT).free<1024**3:reason='disk_reserve'
                elif logs>8*1024**2:reason='log_budget'
                elif time.monotonic()-started>555:reason='supervisor_deadline'
                if reason:break
                time.sleep(2)
        finally:
            subprocess.run(['systemctl','stop',unit],capture_output=True,timeout=30)
            process.wait(timeout=35)
            subprocess.run(['systemctl','reset-failed',unit],capture_output=True,timeout=10)
    assert not any(vm_lab.active(name) for name in vm_lab.NODES)
    for name in vm_lab.NODES:vm_lab.stop(name)
    after=inventory()
    parents=json.loads((ROOT/'parent-disks.json').read_text())
    for name,prior in parents.items():
        path=OLD/name/'disk.qcow2';info=path.stat()
        assert (info.st_size,info.st_mtime_ns,info.st_ino)==(prior['size'],prior['mtime_ns'],prior['inode'])
    result={'node':node,'phase':phase,'exit':process.returncode,'stop_reason':reason,'samples':samples,
            'host_oom_delta':after['kernel_oom_kill_count']-before['kernel_oom_kill_count'],
            'host_listeners_restored':sorted(before['listeners'],key=str)==sorted(after['listeners'],key=str),
            'owned_guests_stopped':True,'original_parent_disk_metadata_unchanged':True,
            'temporary_ram_files_remaining':sum((ROOT/n/'ramfile').exists() for n in vm_lab.NODES)}
    report(ROOT/'results'/('run-'+suffix+'.json'),result)
    print(json.dumps({k:v for k,v in result.items() if k!='samples'}))
    assert process.returncode==0 and reason is None and result['host_oom_delta']==0 and result['host_listeners_restored']


if __name__=='__main__':
    assert os.geteuid()==0
    os.umask(0o077)
    operation=sys.argv[1]
    if operation=='prepare':prepare()
    elif operation=='reset-unstarted':reset_unstarted()
    elif operation=='run' and sys.argv[2]=='entry':run(sys.argv[2])
    elif operation=='install-entry':
        case_id=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:10]
        assert not (ROOT/'results'/('plan-entry-install-'+case_id+'.json')).exists(),'unchanged controller cannot replay a case'
        reset_entry_overlay(case_id);run('entry','install',case_id)
    elif operation=='worker' and sys.argv[2]=='entry':worker(sys.argv[2],sys.argv[3],sys.argv[4])
    else:raise SystemExit('readiness only; install phases not yet authorized by this controller')
