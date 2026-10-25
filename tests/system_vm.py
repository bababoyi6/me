"""Ephemeral-CI KVM primitives; no production node or public listener."""
import argparse
import hashlib
import http.server
import json
import os
from pathlib import Path
import signal
import shutil
import socket
import socketserver
import struct
import subprocess
import sys
import threading
import time

ROOT = Path(os.environ.get('RUNNER_TEMP','/tmp'))/'tdh-audit-system-vms'
CG = Path('/sys/fs/cgroup/tdh-audit-fullsystem-placeholder')
IMAGE = ROOT / 'ubuntu-22.04-minimal-cloudimg-amd64.img'
NODES = {'base': (4, 1024, 22040), 'entry': (1, 1024, 22041), 'backend1': (2, 1024, 22042), 'backend2': (3, 1024, 22043)}
ENV = dict(os.environ)


def run(argv, **kwargs):
    return subprocess.run(list(map(str, argv)), env=ENV, check=True, capture_output=True, timeout=30, **kwargs)


def validate():
    assert os.geteuid() == 0 and ROOT.resolve() == ROOT and not ROOT.is_symlink()
    assert ROOT.stat().st_uid == 0 and not ROOT.stat().st_mode & 0o077


def initialize():
    run(['gpgv','--keyring','/usr/share/keyrings/ubuntu-cloudimage-keyring.gpg',ROOT/'SHA256SUMS.gpg',ROOT/'SHA256SUMS'])
    expected=next(line.split()[0] for line in (ROOT/'SHA256SUMS').read_text().splitlines()
                  if line.split()[1].lstrip('*')==IMAGE.name)
    digest=hashlib.sha256()
    with IMAGE.open('rb') as source:
        for block in iter(lambda:source.read(1024**2),b''): digest.update(block)
    assert len(expected)==64 and digest.hexdigest()==expected
    assert CG.exists(), 'worker must already be resource bounded'
    run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ROOT/'key'])
    key = (ROOT/'key.pub').read_text().strip()
    for name, (index, memory, port) in NODES.items():
        folder = ROOT/name
        folder.mkdir(mode=0o700)
        seed = ROOT/'seed'/name
        seed.mkdir(parents=True,mode=0o700)
        mac = f'52:54:00:71:00:{index:02x}'
        config = {'hostname': 'tdh-test-'+name, 'disable_root':False,'ssh_pwauth':False,
                  'users':[{'name':'root','lock_passwd':True,'shell':'/bin/bash','ssh_authorized_keys':[key]}],
                  'package_update':False,'package_upgrade':False,'manage_etc_hosts':False,
                  'write_files':[{'path':'/etc/hosts','append':True,'content':'\n127.0.0.1 api.ipify.org ipv4.icanhazip.com checkip.amazonaws.com ifconfig.me v4.ident.me\n'}]}
        (seed/'user-data').write_text('#cloud-config\n'+json.dumps(config)+'\n')
        (seed/'meta-data').write_text(json.dumps({'instance-id':'tdh-kvm-'+name,'local-hostname':'tdh-test-'+name}))
        net={'version':2,'ethernets':{
            'wan0':{'match':{'macaddress':'52:54:00:71:00:04'},'set-name':'wan0','dhcp4':True,'dhcp6':False},
            'lan0':{'match':{'macaddress':mac.replace(':00:',':01:')},'set-name':'lan0','dhcp4':False,
                    'addresses':[f'11.254.254.{index}/24']}},'renderer':'networkd'}
        (seed/'network-config').write_text(json.dumps(net))
        run(['/usr/bin/qemu-img','create','-f','qcow2','-F','qcow2','-b',IMAGE,folder/'disk.qcow2','6G'])
    print(json.dumps({'initialized':True,'image_verified':True,'image_sha256':expected,'limits_from_transient_unit':True}))


def state(name):
    return json.loads((ROOT/name/'process.json').read_text())


def qmp(name, command):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(3)
        connection.connect(str(ROOT/name/'qmp'))
        stream=connection.makefile('rwb',buffering=0)
        json.loads(stream.readline())
        for request in ({'execute':'qmp_capabilities'},{'execute':command}):
            stream.write(json.dumps(request).encode()+b'\n')
            while True:
                reply=json.loads(stream.readline())
                if 'return' in reply or 'error' in reply: break
        assert 'error' not in reply


def active(name):
    try:
        value=state(name); pid=value['pid']; proc=Path('/proc')/str(pid)
        return str(ROOT/name/'disk.qcow2').encode() in (proc/'cmdline').read_bytes()
    except (FileNotFoundError,KeyError,ProcessLookupError):
        return False


def start(name):
    assert not active(name)
    assert shutil.disk_usage(ROOT).free > 512*1024**2, 'insufficient lab disk reserve'
    index, memory, port = NODES[name]
    mac = f'52:54:00:71:00:{index:02x}'
    folder = ROOT/name
    (folder/'qmp').unlink(missing_ok=True)
    wan=f'user,id=wan,ipv6=off,hostfwd=tcp:127.0.0.1:{port}-:22'
    if name=='entry': wan+=',hostfwd=tcp:127.0.0.1:22443-:443'
    argv=['/usr/bin/qemu-system-x86_64','-enable-kvm','-cpu','host','-smp','1','-m',str(memory),
          '-object',f'memory-backend-file,id=guestmem,size={memory}M,mem-path={folder}/ramfile,share=on,reserve=off,merge=off,dump=off',
          '-machine','memory-backend=guestmem',
          '-nodefaults','-display','none','-no-reboot','-L','/usr/share/qemu',
          '-bios','/usr/share/seabios/bios-256k.bin',
          '-drive',f'file={folder}/disk.qcow2,format=qcow2,if=virtio,cache=none,discard=unmap,detect-zeroes=unmap',
          '-netdev',wan,'-device','virtio-net-pci,netdev=wan,mac=52:54:00:71:00:04,romfile=',
          '-netdev','socket,id=lan,connect=127.0.0.1:22881','-device',f'virtio-net-pci,netdev=lan,mac={mac.replace(":00:",":01:")},romfile=',
          '-device','virtio-balloon-pci,free-page-reporting=on',
          '-smbios',f'type=1,serial=ds=nocloud-net;s=http://10.0.2.2:22880/{name}/',
          '-serial',f'file:{folder}/console.log','-qmp',f'unix:{folder}/qmp,server=on,wait=off',
          '-sandbox','on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny']
    if (folder/'boot-generic.json').exists():
        boot=json.loads((folder/'boot-generic.json').read_text())
        for key in ('kernel','initrd'):
            path=Path(boot[key])
            assert path.is_relative_to(ROOT/'generic') and path.resolve()==path and path.is_file()
        argv+=['-kernel',boot['kernel'],'-initrd',boot['initrd'],'-append','root=LABEL=cloudimg-rootfs ro console=ttyS0']
    command=['/bin/sh','-c','echo $$ > "$1/cgroup.procs"; shift; exec "$@"','vm',str(CG),*map(str,argv)]
    with (folder/'qemu.log').open('ab') as log:
        process=subprocess.Popen(command,env=ENV,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    (folder/'process.json').write_text(json.dumps({'pid':process.pid,'logical_memory_mib':memory,'vcpus':1}))
    time.sleep(1)
    assert active(name), (folder/'qemu.log').read_text()[-1000:]
    print(json.dumps({'started':name,'logical_memory_mib':memory}))


def stop(name, force=False):
    if active(name):
        qmp(name,'quit' if force else 'system_powerdown')
        deadline=time.monotonic()+(5 if force else 35)
        while active(name) and time.monotonic()<deadline: time.sleep(.1)
        if active(name):
            (ROOT/name/'forced-stop.json').write_text(json.dumps({'forced':True,'reason':'guest shutdown deadline'}))
            qmp(name,'quit')
            deadline=time.monotonic()+5
            while active(name) and time.monotonic()<deadline: time.sleep(.1)
        assert not active(name)
    memory=ROOT/name/'ramfile'
    if memory.exists():
        assert not memory.is_symlink() and memory.is_file() and memory.stat().st_size in (0,NODES[name][1]*1024**2)
        memory.unlink()


class Seed(http.server.SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs): super().__init__(*args,directory=str(ROOT/'seed'),**kwargs)
    def log_message(self,*args): pass


CLIENTS=set()
CLIENT_LOCK=threading.Lock()


class Link(socketserver.BaseRequestHandler):
    def handle(self):
        client=(self.request,threading.Lock())
        with CLIENT_LOCK: CLIENTS.add(client)
        def exact(size):
            data=b''
            while len(data)<size:
                part=self.request.recv(size-len(data))
                if not part: raise EOFError
                data+=part
            return data
        try:
            while True:
                header=exact(4); size=struct.unpack('!I',header)[0]
                assert 0<size<=65535
                packet=header+exact(size)
                with CLIENT_LOCK: peers=list(CLIENTS-{client})
                for peer,lock in peers:
                    try:
                        with lock: peer.sendall(packet)
                    except OSError: pass
        except (OSError,EOFError,AssertionError): pass
        finally:
            with CLIENT_LOCK: CLIENTS.discard(client)


class Switch(socketserver.ThreadingTCPServer):
    allow_reuse_address=True
    daemon_threads=True

