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
from full_system_candidate import expectations,bootstrap,PROTECTED,maintenance

ROOT = vm.ROOT
ROLES = ('entry', 'backend1', 'backend2')
EVIDENCE = ROOT.parent / 'tdh-full-system-evidence'
UNIT = 'tdh-audit-fullsystem.service'
LIMIT_SECONDS = 1500
PROFILE=os.environ.get('TDH_FULL_SYSTEM_PROFILE','published')
if PROFILE not in ('published','candidate'):
    raise ValueError('unknown full-system profile')
EXPECTED=expectations(os.environ.get('TDH_CANDIDATE_REF','')) if PROFILE=='candidate' else None
if EXPECTED is not None:
    MANAGER,CORE=EXPECTED['manager'],EXPECTED['core']


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
        if node=='base':
            # Before installing any project credentials, only fixed OS package
            # logs are safe to export. Never apply this to role transcripts.
            details=ssh(node,"tail -c 8192 /root/preparation.log 2>/dev/null; tail -c 4096 /root/cloud-init-status.log 2>/dev/null\n",20)
            write(ROOT/'results/preparation-failure.json',{'exit':completed.returncode,
                  'public_os_preparation_log':details.stdout[-12288:],
                  'ssh_stderr':completed.stderr[-1024:],
                  'overlay_file_bytes':(ROOT/'base/disk.qcow2').stat().st_size,
                  'qemu_public_error':(ROOT/'base/qemu.log').read_text()[-4096:]})
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
apt-get -o Acquire::ForceIPv4=true -o Acquire::Retries=1 install -y --no-install-recommends ca-certificates curl jq python3 openssl wireguard-tools iproute2 iputils-ping netcat-openbsd qrencode socat util-linux procps kmod tar gzip haproxy linux-image-virtual >>/root/preparation.log 2>&1
apt-get clean
systemctl disable --now haproxy.service >/root/base-haproxy-stop.log 2>&1
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
          'preinstalled_packages': True, 'kernel_meta_package':'linux-image-virtual',
          'production_installer_changes_kernel': False})


def prepare_lan(node):
    """A cloned NoCloud netplan must not retain the base guest's LAN match."""
    index=ROLES.index(node)+1
    script="""python3 - <<'PY'
import json,subprocess
from pathlib import Path
import yaml
index=INDEX
mac=f'52:54:01:71:01:{index:02x}'
interfaces=[p.name for p in Path('/sys/class/net').iterdir() if (p/'address').read_text().strip()==mac]
assert len(interfaces)==1
device=interfaces[0]
before=json.loads(subprocess.check_output(['ip','-j','address'],text=True))
for path in Path('/etc/netplan').glob('*.yaml'):
 data=yaml.safe_load(path.read_text())
 entries=data.get('network',{}).get('ethernets',{})
 remove=[name for name,row in entries.items() if str(row.get('match',{}).get('macaddress','')).lower().startswith('52:54:01:71:01:')]
 if remove:
  for name in remove:del entries[name]
  path.write_text(yaml.safe_dump(data));path.chmod(0o600)
config={'network':{'version':2,'renderer':'networkd','ethernets':{'tdh_lab':{
 'match':{'macaddress':mac},'dhcp4':False,'dhcp6':False,'addresses':[f'11.254.254.{index}/24']}}}}
path=Path('/etc/netplan/60-tdh-lab.yaml');path.write_text(yaml.safe_dump(config));path.chmod(0o600)
# Apply just the attested lab NIC live; leave the SSH/WAN interface untouched.
addresses=next(r for r in before if r['ifname']==device).get('addr_info',[])
assert all(a.get('family')!='inet' or a['local'].startswith('11.254.254.') for a in addresses)
subprocess.run(['ip','-4','address','flush','dev',device],check=True)
subprocess.run(['ip','address','add',f'11.254.254.{index}/24','dev',device],check=True)
subprocess.run(['ip','link','set',device,'up'],check=True)
subprocess.run(['netplan','generate'],check=True,capture_output=True)
# The discard route prevents synthetic "public" fixture addresses escaping
# through SLIRP if the lab NIC is unavailable. This is guest-only routing.
unit=Path('/etc/systemd/system/tdh-lab-route-guard.service')
unit.write_text('[Unit]\\nDefaultDependencies=no\\nAfter=local-fs.target\\nBefore=network-pre.target\\nWants=network-pre.target\\n[Service]\\nType=oneshot\\nRemainAfterExit=yes\\nExecStart=/usr/sbin/ip route replace blackhole 11.254.254.0/24 metric 32760\\n[Install]\\nWantedBy=multi-user.target\\n')
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','enable','--now','tdh-lab-route-guard.service'],check=True,capture_output=True)
route=json.loads(subprocess.check_output(['ip','-j','route','get',f'11.254.254.{2 if index==1 else 1}'],text=True))[0]
assert route['dev']==device and route['prefsrc']==f'11.254.254.{index}'
print(json.dumps({'complete':True,'before':before,'device':device,'route':route,'synthetic_wan_escape_blocked':True}))
PY
""".replace('INDEX',str(index))
    return json.loads(guest(node,script,45))


def interactive(node, name, line, action):
    import pexpect
    child = None
    progress={'node':node,'operation':name,'complete':False,'expected':None,'known_markers':[]}
    try:
        with (ROOT/'private'/(name+'.log')).open('w') as log:
            child = pexpect.spawn(SSH[0], [*SSH[1:], '-tt', '-p', str(ports[node]), 'root@127.0.0.1', line],
                                  encoding='utf8', codec_errors='replace', timeout=180)
            child.logfile_read = log
            original_expect=child.expect
            def observe(pattern,*values,**options):
                progress['expected']=str(pattern)
                write(ROOT/'results'/('interaction-'+name+'.json'),progress)
                return original_expect(pattern,*values,**options)
            child.expect=observe
            action(child)
            child.expect(pexpect.EOF, timeout=30)
            child.close()
            assert child.exitstatus == 0, 'interactive operation failed'
            progress['complete']=True
    finally:
        if child is not None and child.isalive():
            child.close(force=True)
        transcript=(ROOT/'private'/(name+'.log')).read_text() if (ROOT/'private'/(name+'.log')).exists() else ''
        for marker in ('安装所需的 Ubuntu 软件包','请输入入口 VPS 公网 IPv4','请输入后端 VPS 公网 IPv4',
                       '检测到已有 haproxy.service','无法验证','按 Enter 返回控制面板','请选择','[失败]'):
            if marker in transcript:progress['known_markers'].append(marker)
        # Export only installer error lines, never links, join codes or QR data.
        clean=re.sub(r'\x1b\[[0-9;]*[A-Za-z]','',transcript)
        lines=[line for line in clean.splitlines() if '[失败]' in line or line.startswith(('curl:','ssh:','E:'))]
        safe='\n'.join(lines)[-2048:]
        safe=re.sub(r'(?:TDH4|TDHR4)\.[A-Za-z0-9_-]+','[redacted-code]',safe)
        safe=re.sub(r'[A-Za-z0-9_+/=-]{32,}','[redacted-token]',safe)
        progress['sanitized_error_lines']=safe
        write(ROOT/'results'/('interaction-'+name+'.json'),progress)


def install(node):
    if EXPECTED is not None:
        line=bootstrap(EXPECTED)
    else:
        text = (Path(__file__).resolve().parents[1]/'README.md').read_text()
        line = text.split('```bash\n', 1)[1].split('\n```', 1)[0]
        assert 'https://raw.githubusercontent.com/bababoyi6/me/main/telemt-dual-hop.sh' in line
    def action(child):
        child.expect('请选择'); child.sendline(str(ROLES.index(node)+1))
        if node != 'entry':
            child.expect('Join Code: ')
            child.sendline(json.loads((ROOT/'private/enrollment.json').read_text())[node])
        assert child.expect(['请输入'+('入口 VPS 公网 IPv4' if node=='entry' else '后端 VPS 公网 IPv4')+': ',
                             r'\[失败\]','按 Enter 返回控制面板'])==0,'installer stopped before address input'
        child.sendline('11.254.254.'+str(ROLES.index(node)+1))
        if node != 'entry' and EXPECTED is not None:
            # Observe startup before a failed installer removes its owned files.
            # Export only fixed service fields and API counts, never links/logs.
            sampler=threading.Thread(target=startup_observer,args=(node,),daemon=True)
            sampler.start()
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


def startup_observer(node):
    rows=[]
    for _ in range(20):
        try:
            value=ssh(node,"""python3 - <<'PY'
import json,subprocess,urllib.request,urllib.error
from pathlib import Path
result={}
for unit in ('telemt-dual-hop-telemt.service','wg-quick@tdh-b1.service','wg-quick@tdh-b2.service'):
 p=subprocess.run(['systemctl','show',unit,'--property=ActiveState,SubState,Result,ExecMainStatus,NRestarts'],capture_output=True,text=True,timeout=3)
 result[unit]=dict(line.split('=',1) for line in p.stdout.splitlines() if '=' in line)
token=Path('/etc/telemt-dual-hop/telemt/api.token')
if token.is_file():
 try:
  opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
  request=urllib.request.Request('http://127.0.0.1:19091/v1/users',headers={'Authorization':'Bearer '+token.read_text().strip()})
  with opener.open(request,timeout=2) as response:
   data=json.loads(response.read(65536))
   result['api']={'status':response.status,'users':len(data.get('data',[])),'tls_link_counts':[len(u.get('links',{}).get('tls',[])) for u in data.get('data',[])]}
 except (OSError,ValueError,urllib.error.URLError) as e:result['api']={'error_type':type(e).__name__}
# Map the journal to fixed booleans; no journal text leaves this process.
p=subprocess.run(['journalctl','-u','telemt-dual-hop-telemt.service','-n','40','--no-pager','-o','cat'],capture_output=True,text=True,timeout=3)
text=p.stdout.lower()
result['startup_markers']={k:k in text for k in ('permission denied','os error 13','panicked','address already in use','failed to parse','unknown field','tls','error','warn')}
print(json.dumps(result))
PY
""",20)
            if value.returncode==0:
                rows.append(json.loads(value.stdout))
                write(ROOT/'results'/('startup-'+node+'.json'),rows)
        except (OSError,ValueError,subprocess.SubprocessError):pass
        time.sleep(5)


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


def failure_diagnostics(node):
    """Read-only, allow-listed service evidence; never journals or link data."""
    report={}
    perf=ssh(node,'a perf 2\n',25)
    if perf.returncode==0:
        report['performance']=json.loads(perf.stdout)
    else:
        report['performance_exit']=perf.returncode
    diag=ssh(node,'a diagnose\n',35)
    text=re.sub(r'\x1b\[[0-9;]*[A-Za-z]','',diag.stdout+diag.stderr)
    report['diagnose_exit']=diag.returncode
    report['diagnose']=re.sub(r'[A-Za-z0-9_+/=-]{32,}','[redacted-token]',text[-8192:])
    if EXPECTED is not None:
        errors=ssh(node,"""python3 - <<'PY'
import json,re
from pathlib import Path
names=('core-upgrade.log','core-repeat.log','core-rollback.log','core-rollback-repeat.log','core-final-upgrade.log','health-refresh.log')
rows={}
for name in names:
 p=Path('/root')/name
 if not p.is_file():continue
 text=p.read_text(errors='replace')[-8192:]
 text=re.sub(r'\\x1b\\[[0-9;]*[A-Za-z]','',text)
 lines=[s for s in text.splitlines() if '[失败]' in s or '核心事务失败' in s or s.startswith(('curl:','sha256sum:'))]
 rows[name]=re.sub(r'[A-Za-z0-9_+/=-]{32,}','[redacted-token]','\\n'.join(lines)[-2048:])
print(json.dumps(rows))
PY
""",20)
        if errors.returncode==0:report['maintenance_errors']=json.loads(errors.stdout)
    if node!='entry':
        value=ssh(node,"""python3 - <<'PY'
import concurrent.futures,json,socket,subprocess,time,urllib.request,urllib.error
from pathlib import Path
addresses=('149.154.175.50','149.154.167.51','149.154.175.100','149.154.167.91','149.154.171.5')
def probe(item):
 index,address=item;start=time.monotonic()
 try:
  with socket.create_connection((address,443),timeout=2):pass
  return {'dc_index':index,'tcp_connected':True,'elapsed_sec':time.monotonic()-start}
 except OSError as e:return {'dc_index':index,'tcp_connected':False,'error_type':type(e).__name__}
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):return None
result={}
try:
 token=Path('/etc/telemt-dual-hop/telemt/api.token').read_text().strip()
 request=urllib.request.Request('http://127.0.0.1:19091/v1/health/ready',headers={'Authorization':'Bearer '+token})
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
 with opener.open(request,timeout=2) as response:
  payload=json.loads(response.read(16384))
  result['api']={'http_status':response.status,'ok':payload.get('ok') is True,'ready':payload.get('data',{}).get('ready') is True}
except (OSError,ValueError,urllib.error.URLError) as e:result['api']={'error_type':type(e).__name__}
with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:result['direct_dc_tcp']=list(pool.map(probe,enumerate(addresses,1)))
raw=subprocess.run(['wg','show','all','latest-handshakes'],capture_output=True,text=True,timeout=3)
result['wg_handshake_timestamps']=[int(line.split()[-1]) for line in raw.stdout.splitlines()]
print(json.dumps(result))
PY
""",20)
        if value.returncode==0:report['upstream']=json.loads(value.stdout)
        else:report['upstream_snapshot_exit']=value.returncode
    write(ROOT/'results'/('diagnostics-'+node+'.json'),report)


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
    if EXPECTED is not None:
        result.update(profile='candidate',source_ref=EXPECTED['source_ref'],pinned_manager_download=True)
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
            guest(node,'cloud-init status --wait >/root/firstboot-status.log 2>&1\n',90)
        phase('attest-private-lan-before-installs')
        lan={node:prepare_lan(node) for node in ROLES}
        for node in ROLES:
            for peer in ROLES:
                if peer!=node:
                    guest(node,'ping -n -c 1 -W 2 11.254.254.'+str(ROLES.index(peer)+1)+' >/root/private-lan-ping.log\n',15)
        write(ROOT/'results/lan.json',{'complete':True,'all_six_directed_lan_paths_verified':True,'nodes':lan})
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
        if EXPECTED is not None:
            phase('candidate-repeat-install-preservation')
            result['repeat_installs']={}
            for node in ROLES:
                before=json.loads(guest(node,PROTECTED))
                core_before=identity(node)
                def repeat_action(child):
                    child.expect('请选择');child.sendline('0')
                interactive(node,node+'-repeat-install',bootstrap(EXPECTED),repeat_action)
                after=json.loads(guest(node,PROTECTED))
                assert before==after and identity(node)==core_before,'repeat install changed node identity or config'
                result['repeat_installs'][node]={'files_unchanged':True,'protected_files':after['files'],
                                                'core_unchanged':True,'manager_unchanged':True}
            phase('candidate-native-maintenance')
            result['native_maintenance']=maintenance(guest,EXPECTED)
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
        for node in result['roles']:
            try:failure_diagnostics(node)
            except (OSError,ValueError,subprocess.SubprocessError):pass
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
    maximum = min(3*1024**3, (info['MemAvailable']-reserve)*3//4)
    assert info['MemTotal']>=8*1024**3 and maximum>=2560*1024**2
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
                '-p','MemorySwapMax=0','-p','OOMPolicy=stop','-p','TasksMax=256','-p',f'RuntimeMaxSec={LIMIT_SECONDS}',
                '-p','TimeoutStopSec=15','-p','KillMode=control-group','-p','IOWeight=10',
                '-p','LimitFSIZE=3221225472','--setenv=RUNNER_TEMP='+str(ROOT.parent),
                *(['--setenv=TDH_FULL_SYSTEM_PROFILE=candidate',
                   '--setenv=TDH_CANDIDATE_REF='+EXPECTED['source_ref']] if EXPECTED is not None else []),
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
        for name in ('worker.json','e2e.json','preparation.json','preparation-failure.json','lan.json',
                     'interaction-entry-install.json','interaction-backend1-install.json',
                     'interaction-backend2-install.json','interaction-backend1-registration.json',
                     'interaction-backend2-registration.json','diagnostics-entry.json',
                     'diagnostics-backend1.json','diagnostics-backend2.json',
                     'interaction-entry-repeat-install.json','interaction-backend1-repeat-install.json',
                     'interaction-backend2-repeat-install.json','startup-backend1.json','startup-backend2.json'):
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
        validate(worker_report,report,EXPECTED)
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
