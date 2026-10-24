#!/usr/bin/env python3
"""Bounded logical idle clients plus two small file flows, private loopback only."""
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import struct
import time

from e2e_lab import FullLab, close_on_error
from lab import exact
from mtproto_transport import mtproxy_connect
from network_lab import memory_available
from telemt_probe import SECRETS


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True)
    parser.add_argument('--haproxy',default='haproxy')
    parser.add_argument('--clients',type=int,choices=(30,60,100),default=30)
    args=parser.parse_args()
    root=Path(args.root)
    if root.is_symlink() or root.resolve()!=root or not root.name.startswith('tdh-audit-'):
        parser.error('dedicated absolute lab directory required')
    if os.geteuid()!=0 or root.stat().st_uid!=0 or root.stat().st_mode & 0o077:
        parser.error('root-owned private lab directory required')
    signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
    metadata=json.loads((root/'auto-build.json').read_text())
    result={'classification':'SINGLE_VPS_SYNTHETIC_LOOPBACK_MIXED','logical_clients':args.clients,
            'mock_dc_socket_timeout_seconds':25,'client_socket_buffers_requested':32768,
            'scope':'idle authenticated synthetic requests waiting for final ACK; two active file flows; not Telegram users',
            'complete':False,'idle_digest_verified':0,'bulk':[],'small_probes':[],'small_probe_errors':[]}
    destination=root/'results'/('mixed-'+str(args.clients)+'-'+str(time.time_ns())+'.json')
    lab=None
    held=[]
    def save():
        destination.parent.mkdir(exist_ok=True)
        destination.write_text(json.dumps(result,indent=2)+'\n')
    try:
        save()
        lab=FullLab(root/'telemt-auto',args.haproxy,experimental_binary=(metadata['sha256'],metadata['version']),
                    artifact_registry=root/'artifact-registry')
        lab.dc.socket_timeout=25
        started=time.monotonic()
        for index in range(args.clients):
            if time.monotonic()-started>12 or memory_available()<256*1024**2:
                raise RuntimeError('idle-client setup safety stop')
            sock=socket.create_connection(('127.0.0.1',lab.front),timeout=5)
            held.append((sock,None))
            sock.settimeout(5)
            sock.setsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF,32768)
            sock.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,32768)
            domain=list(SECRETS)[index%2]
            stream=mtproxy_connect(sock,domain,SECRETS[domain])
            stream.sendall(struct.pack('!cQQ',b'u',1,125000))
            stream.sendall(b'x')
            assert exact(stream,32)==hashlib.sha256(b'x').digest()
            held[-1]=(sock,stream)
            result['idle_digest_verified']+=1
        result['idle_setup_seconds']=time.monotonic()-started
        save()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool,close_on_error(lab):
            futures=[pool.submit(lab.transfer,domain,'D' if i==0 else 'U',1048576,250000,
                                 persistent=True,io_timeout=8,send_buffer=262144) for i,domain in enumerate(SECRETS)]
            while not all(f.done() for f in futures):
                if time.monotonic()-started>20 or memory_available()<256*1024**2:
                    raise RuntimeError('mixed-client safety stop')
                try:
                    result['small_probes'].append(lab.transfer('apple.com','D',1024,125000))
                except Exception as exc:
                    result['small_probe_errors'].append(type(exc).__name__)
                time.sleep(.3)
            result['bulk']=[f.result() for f in futures]
        for sock,stream in held:
            stream.sendall(b'ACK!')
            assert exact(stream,2)==b'OK'
            sock.close()
        lab.validate(args.clients+len(result['bulk'])+len(result['small_probes']))
        if result['small_probe_errors']:
            raise RuntimeError('small probe failed')
        result['complete']=True
        result['elapsed_seconds']=time.monotonic()-started
    except BaseException as exc:
        result['failure_type']=type(exc).__name__
        raise
    finally:
        for sock,_ in held:
            sock.close()
        if lab:
            lab.close()
        save()
    print(json.dumps({'result':str(destination),'complete':True,'logical_clients':args.clients}))


if __name__=='__main__':
    main()
