"""One bounded KVM cohort for the published installer; exports JSON only.

Prepared Ubuntu guests share a CI host. This is a functional systemd/reboot
check, never a measurement of independent VPS capacity or public-line speed.
"""
import fcntl
import hashlib
import http.server
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

import system_vm as vm
from system_vm_ssh import SSH, ports, ssh
from full_system_contract import validate, MANAGER, CORE

ROOT = vm.ROOT
ROLES = ('entry', 'backend1', 'backend2')
EVIDENCE = ROOT.parent / 'tdh-full-system-evidence'
UNIT = 'tdh-audit-fullsystem.service'
LIMIT_SECONDS = 1500


def write(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def command(argv, seconds=30):
    return subprocess.run(argv, check=True, capture_output=True, text=True, timeout=seconds)


def kvm_gate():
    # Test usable nested virtualization before any image/package download.
    with open('/dev/kvm', 'rb') as device:
        assert fcntl.ioctl(device, 0xAE00, 0) == 12
        descriptor = fcntl.ioctl(device, 0xAE01, 0)
        os.close(descriptor)


def guest(node, script, seconds=60):
    completed = ssh(node, script, seconds)
    if completed.returncode:
        (ROOT / 'private' / (node + '-command.log')).write_text(completed.stdout + completed.stderr)
        raise RuntimeError('private guest command failed: ' + node)
    return completed.stdout


def ready(node, seconds=180):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            value = json.loads(guest(node, "python3 - <<'PY'\nimport json,socket\nfrom pathlib import Path\nprint(json.dumps({'host':socket.gethostname(),'pid1':Path('/proc/1/comm').read_text().strip(),'boot':Path('/proc/sys/kernel/random/boot_id').read_text().strip()}))\nPY\n", 20))
            assert value['host'] == 'tdh-test-' + node and value['pid1'] == 'systemd'
            return value['boot']
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError):
            time.sleep(2)
    raise TimeoutError('guest readiness: ' + node)


def poweroff(nodes):
    for node in nodes:
        vm.qmp(node, 'system_powerdown')
    deadline = time.monotonic() + 120
    while any(vm.active(n) for n in nodes) and time.monotonic() < deadline:
        time.sleep(.5)
    assert not any(vm.active(n) for n in nodes), 'normal shutdown deadline'
    for node in nodes:
        vm.stop(node)


def prepare_base():
    vm.start('base')
    ready('base')
    guest('base', '''set -Eeuo pipefail
cloud-init status --wait >/root/cloud-init-status.log 2>&1
export DEBIAN_FRONTEND=noninteractive
apt-get -o Acquire::ForceIPv4=true -o Acquire::Retries=1 update -qq >/root/preparation.log 2>&1
apt-get -o Acquire::ForceIPv4=true -o Acquire::Retries=1 install -y --no-install-recommends ca-certificates curl jq python3 openssl wireguard-tools iproute2 iputils-ping netcat-openbsd qrencode socat util-linux procps kmod tar gzip haproxy linux-image-generic >>/root/preparation.log 2>&1
apt-get clean
''', 480)
    version = guest('base', "ls /boot/vmlinuz-*-generic\n").strip().removeprefix('/boot/vmlinuz-')
    assert re.fullmatch(r'[0-9][0-9.\-]*-generic', version)
    folder = ROOT / 'generic' / version
    folder.mkdir(parents=True, mode=0o700)
    for source, target in ((f'/boot/vmlinuz-{version}', folder/'vmlinuz'), (f'/boot/initrd.img-{version}', folder/'initrd')):
        command(['scp', '-q', *SSH[1:], '-P', str(ports['base']), 'root@127.0.0.1:' + source, str(target)], 60)
        target.chmod(0o600)
    boot = {'version': version, 'kernel': str(folder/'vmlinuz'), 'initrd': str(folder/'initrd')}
    guest('base', 'cloud-init clean --logs --machine-id\nsync\n')
    poweroff(('base',))
    for node in ROLES:
        disk = ROOT/node/'disk.qcow2'
        # These are never-started, newly created overlays, not user disks.
        assert not (ROOT/node/'process.json').exists() and disk.is_file() and not disk.is_symlink()
        disk.unlink()
        command(['qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2', '-b', str(ROOT/'base/disk.qcow2'), str(disk), '6G'])
        write(ROOT/node/'boot-generic.json', boot)
    write(ROOT/'results/preparation.json', {'complete': True, 'kernel': version, 'factory_clean': False,
          'prepared_once_then_cloned': True, 'guest_logical_mib_each': 1024, 'guest_vcpus_each': 1,
          'preinstalled_packages': True, 'production_installer_changes_kernel': False})


def interactive(node, name, line, action):
    import pexpect
    child = None
    try:
        with (ROOT/'private'/(name+'.log')).open('w') as log:
            child = pexpect.spawn(SSH[0], [*SSH[1:], '-tt', '-p', str(ports[node]), 'root@127.0.0.1', line],
                                  encoding='utf8', codec_errors='replace', timeout=180)
            child.logfile_read = log
            action(child)
            child.expect(pexpect.EOF, timeout=30)
            child.close()
            assert child.exitstatus == 0, 'interactive operation failed'
    finally:
        if child is not None and child.isalive():
            child.close(force=True)


def install(node):
    text = (Path(__file__).resolve().parents[1]/'README.md').read_text()
    line = text.split('```bash\n', 1)[1].split('\n```', 1)[0]
    assert 'https://raw.githubusercontent.com/bababoyi6/me/main/telemt-dual-hop.sh' in line
    def action(child):
        child.expect('请选择'); child.sendline(str(ROLES.index(node)+1))
        if node != 'entry':
            child.expect('Join Code: ')
            child.sendline(json.loads((ROOT/'private/enrollment.json').read_text())[node])
        child.expect('请输入'+('入口 VPS 公网 IPv4' if node=='entry' else '后端 VPS 公网 IPv4')+': ')
        child.sendline('11.254.254.'+str(ROLES.index(node)+1))
        matched = child.expect(['按 Enter 返回控制面板', r'\[失败\]'], timeout=240)
        assert matched == 0, 'installer failure; private transcript excluded from artifact'
        child.sendline(''); child.expect('请选择'); child.sendline('0')
    interactive(node, node+'-install', line, action)
    transcript = (ROOT/'private'/(node+'-install.log')).read_text()
    if node == 'entry':
        codes = re.findall(r'TDH4\.[A-Za-z0-9_-]+', transcript)
        assert len(codes) == 2
        write(ROOT/'private/enrollment.json', dict(zip(ROLES[1:], codes)))
    else:
        codes = re.findall(r'TDHR4\.[A-Za-z0-9_-]+', transcript)
        assert len(codes) == 1
        (ROOT/'private'/(node+'-receipt')).write_text(codes[0])


def identity(node):
    return json.loads(guest(node, """python3 - <<'PY'
import hashlib,json,shlex,subprocess
from pathlib import Path
d={}
for line in Path('/etc/telemt-dual-hop/state.env').read_text().splitlines():
 k,_,v=line.partition('=')
 if k in ('TDH_ROLE','INSTALLED_VERSION'): d[k]=shlex.split(v)[0]
p=Path('/usr/local/lib/telemt-dual-hop/telemt')
print(json.dumps({'role':d['TDH_ROLE'],'installed_version':d['INSTALLED_VERSION'],
 'pid1':Path('/proc/1/comm').read_text().strip(),
 'manager_sha256':hashlib.sha256(Path('/usr/local/sbin/telemt-dual-hop').read_bytes()).hexdigest(),
 'core_sha256':hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None,
 'core_version':subprocess.check_output([str(p),'--version'],user='telemt',group='telemt',text=True).strip() if p.exists() else None,
 'license_retained':(p.parent/'core-source-notice/TELEMT-LICENSE.txt').is_file()}))
PY
"""))


SNAPSHOT = """python3 - <<'PY'
import hashlib,json
from pathlib import Path
paths=[]
for name in ('/etc/telemt-dual-hop','/etc/wireguard','/usr/local/lib/telemt-dual-hop'):
 paths.extend(p for p in Path(name).rglob('*') if p.is_file() and not p.is_symlink())
paths.extend(p for p in Path('/etc/systemd/system').glob('*telemt-dual-hop*.service') if p.is_file())
paths.extend((Path('/usr/local/sbin/telemt-dual-hop'),Path('/usr/local/bin/a')))
d={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
assert d
Path('/root/reboot-snapshot.json').write_text(json.dumps(d))
PY
"""


def worker():
    os.umask(0o077)
    relative = next(x.split(':', 2)[2] for x in Path('/proc/self/cgroup').read_text().splitlines() if x.startswith('0::'))
    assert relative.endswith('/'+UNIT)
    vm.CG = Path('/sys/fs/cgroup'+relative)
    vm.validate(); vm.initialize()
    for name in ('results', 'private'):
        (ROOT/name).mkdir(mode=0o700)
    result = {'complete': False, 'roles': {}, 'download_boundary_substituted': False,
              'production_accessed': False, 'shared_ci_host': True, 'capacity_benchmark': False}
    def phase(name):
        result['phase'] = name; write(ROOT/'results/worker.json', result)
        print('Full-system phase: '+name, flush=True)
    servers = (http.server.ThreadingHTTPServer(('127.0.0.1', 22880), vm.Seed), vm.Switch(('127.0.0.1', 22881), vm.Link))
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        phase('prepare-base-os'); prepare_base()
        for node in ROLES:
            vm.start(node)
        boots = {node: ready(node) for node in ROLES}
        for node in ROLES:
            phase('public-install-'+node); install(node)
            result['roles'][node] = identity(node)
            assert result['roles'][node]['manager_sha256'] == MANAGER
            if node != 'entry':
                assert result['roles'][node]['core_sha256'] == CORE
        for node in ROLES[1:]:
            phase('register-'+node)
            def action(child):
                import pexpect
                child.expect('TDHR4 回执码: '); child.sendline((ROOT/'private'/(node+'-receipt')).read_text())
                assert child.expect([pexpect.EOF, r'\[失败\]'], timeout=150) == 0
            interactive('entry', node+'-registration', 'a register', action)
        phase('real-dc-and-backend-failover')
        completed = subprocess.run([sys.executable, str(Path(__file__).with_name('system_vm_e2e.py'))], capture_output=True, timeout=220)
        (ROOT/'private/e2e.log').write_bytes(completed.stdout+completed.stderr)
        e2e = json.loads((ROOT/'results/e2e.json').read_text())
        assert completed.returncode == 0 and e2e['complete'] and e2e['actual_backend_stop']
        # Routing must prove the stopped primary carried no new sessions.
        failed = next(s for s in e2e['stages'] if s['stage']=='actual_backend1_telemt_stopped')
        assert all(r['session_delta']==0 for r in failed['routing'] if r['server']=='vps1')
        assert all(r['session_delta']>=1 for r in failed['routing'] if r['server']=='vps2')
        result['e2e_failover_verified'] = True
        for node in ROLES:
            guest(node, 'a diagnose >/root/diagnose.log 2>&1\n', 90)
            guest(node, SNAPSHOT)
        phase('normal-poweroff'); poweroff(ROLES)
        phase('reboot-recovery')
        for node in ROLES:
            vm.start(node)
        for node in ROLES:
            boot = ready(node)
            guest(node, """python3 - <<'PY'
import hashlib,json
from pathlib import Path
d=json.loads(Path('/root/reboot-snapshot.json').read_text())
assert all(Path(p).is_file() and hashlib.sha256(Path(p).read_bytes()).hexdigest()==v for p,v in d.items())
PY
""")
            result['roles'][node].update(new_boot_id=boot!=boots[node], reboot_files_unchanged=True)
        deadline = time.monotonic()+90
        for node in ROLES:
            while True:
                if ssh(node, 'a diagnose >/root/reboot-diagnose.log 2>&1\n', 30).returncode == 0:
                    break
                assert time.monotonic() < deadline, 'post-boot readiness deadline'
                time.sleep(2)
        phase('post-reboot-two-sni')
        from mtproto_transport import mtproxy_connect, req_pq
        credentials = json.loads(guest('entry', "python3 - <<'PY'\nimport json,shlex\nfrom pathlib import Path\nd={}\nfor line in Path('/etc/telemt-dual-hop/state.env').read_text().splitlines():\n k,_,v=line.partition('=')\n if k in ('SECRET_A','SECRET_B'):d[k]=shlex.split(v)[0]\nprint(json.dumps(d))\nPY\n"))
        for domain, key in (('apple.com','SECRET_A'), ('gs.apple.com','SECRET_B')):
            with socket.create_connection(('127.0.0.1', 22443), timeout=15) as connection:
                connection.settimeout(15)
                assert req_pq(mtproxy_connect(connection, domain, bytes.fromhex(credentials[key]), fragment=True))
        result['post_reboot_sni_cases'] = 2
        phase('final-normal-poweroff'); poweroff(ROLES)
        result.update(complete=True, phase='complete')
        write(ROOT/'results/worker.json', result)
    except BaseException as error:
        result.update(error_type=type(error).__name__)
        write(ROOT/'results/worker.json', result)
        raise
    finally:
        for server in servers:
            server.shutdown(); server.server_close()
        for node in vm.NODES:
            vm.stop(node, force=True)


def meminfo():
    return {k.rstrip(':'): int(v.split()[0])*1024 for k,v in (line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())}


def host_oom():
    return int(next(line.split()[1] for line in Path('/proc/vmstat').read_text().splitlines() if line.startswith('oom_kill ')))


def supervise():
    assert os.geteuid()==0 and ROOT.resolve()==ROOT and not ROOT.is_symlink()
    kvm_gate()
    assert ROOT.is_dir() and not EVIDENCE.exists()
    info = meminfo(); reserve = max(2*1024**3, info['MemTotal']//3)
    maximum = min(2*1024**3, (info['MemAvailable']-reserve)*3//4)
    assert info['MemTotal']>=6*1024**3 and maximum>=1536*1024**2
    assert shutil.disk_usage(ROOT).free>=10*1024**3
    EVIDENCE.mkdir(mode=0o755)
    startoom = host_oom()
    report = {'exit': None, 'stop_reason': None, 'cleanup_verified': False, 'host_oom_delta': 0,
              'cpu_quota_percent':60, 'memory_max':maximum, 'memory_high':maximum*3//4,
              'memory_swap_max':0, 'host_reserve':reserve, 'runtime_max_sec':LIMIT_SECONDS,
              'loopback_only_host_listeners':True, 'samples':[]}
    started = time.monotonic()
    process = None
    try:
        with (EVIDENCE/'phases.log').open('w') as log:
            process = subprocess.Popen(['systemd-run','--quiet','--wait','--pipe','--collect','--unit='+UNIT,
                '-p','CPUQuota=60%', '-p',f'MemoryMax={maximum}', '-p',f'MemoryHigh={maximum*3//4}',
                '-p','MemorySwapMax=0','-p','TasksMax=256','-p',f'RuntimeMaxSec={LIMIT_SECONDS}',
                '-p','TimeoutStopSec=15','-p','KillMode=control-group','-p','IOWeight=10',
                '-p','LimitFSIZE=3221225472','--setenv=RUNNER_TEMP='+str(ROOT.parent),
                sys.executable, str(Path(__file__).resolve()), '--worker'], stdout=log, stderr=subprocess.DEVNULL)
            cg = Path('/sys/fs/cgroup/system.slice')/UNIT
            lastsample = 0
            while process.poll() is None:
                elapsed = time.monotonic()-started
                available = meminfo()['MemAvailable']
                row = {'elapsed_sec':round(elapsed,1), 'host_available':available}
                if cg.exists():
                    try:
                        row['memory_current'] = int((cg/'memory.current').read_text())
                        row['events'] = dict(line.split() for line in (cg/'memory.events').read_text().splitlines())
                        assert int(row['events'].get('oom_kill','0'))==0, 'unit OOM'
                    except FileNotFoundError:
                        pass
                assert available>=reserve, 'host memory reserve'
                assert host_oom()==startoom, 'host OOM'
                assert elapsed<=LIMIT_SECONDS+20, 'supervisor deadline'
                assert shutil.disk_usage(ROOT).free>=2*1024**3, 'host disk reserve'
                assert sum(p.stat().st_size for p in ROOT.rglob('*.log'))<=8*1024**2, 'log cap'
                if elapsed-lastsample>=5:
                    report['samples'].append(row); lastsample=elapsed
                time.sleep(2)
            report['exit'] = process.returncode
    except BaseException as error:
        report['stop_reason'] = str(error) if isinstance(error, AssertionError) else type(error).__name__
    finally:
        subprocess.run(['systemctl','stop',UNIT], capture_output=True, timeout=25)
        if process is not None:
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
        report['host_oom_delta'] = host_oom()-startoom
        report['elapsed_sec'] = round(time.monotonic()-started,1)
        # Only fixed-name JSON outputs may cross the private export boundary.
        for name in ('worker.json','e2e.json','preparation.json'):
            source = ROOT/'results'/name
            if source.is_file() and not source.is_symlink():
                assert source.stat().st_size<1024**2
                value = json.loads(source.read_text())
                write(EVIDENCE/name,value)
        all_stopped = not any(vm.active(n) for n in vm.NODES)
        assert all_stopped, 'never remove disks of a live VM'
        assert ROOT.resolve()==ROOT and ROOT.name=='tdh-audit-system-vms' and not ROOT.is_symlink()
        shutil.rmtree(ROOT)
        report['cleanup_verified'] = all_stopped and not ROOT.exists()
        write(EVIDENCE/'supervisor.json',report)
    worker_report = json.loads((EVIDENCE/'worker.json').read_text()) if (EVIDENCE/'worker.json').exists() else {}
    try:
        validate(worker_report,report)
        accepted = True
    except ValueError:
        accepted = False
    write(EVIDENCE/'assessment.json', {'accepted':accepted, 'complete_worker_required':True,
          'private_keys_disks_seeds_transcripts_exported':False, 'production_accessed':False})
    assert accepted, 'full-system acceptance incomplete; see sanitized JSON'


if __name__ == '__main__':
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if sys.argv[1:] == ['--gate']: kvm_gate()
    elif sys.argv[1:] == ['--worker']: worker()
    elif not sys.argv[1:]: supervise()
    else: raise SystemExit('unsupported mode')
