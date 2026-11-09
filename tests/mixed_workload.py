"""Bounded real transport workload; messages are synthetic, not Telegram users."""
import concurrent.futures
import hashlib
import socket
import struct
import threading
import time

from e2e_lab import FullLab, sanitize_startup_log
from lab import exact
from mtproto_transport import mtproxy_connect
from telemt_probe import SECRETS

END=b'\xff'*64
PAYLOAD=16*1024**2


def exchange(stream, sequence):
    payload=struct.pack('!Q',sequence)+hashlib.sha256(str(sequence).encode()).digest()+bytes(24)
    started=time.monotonic()
    stream.sendall(payload)
    if exact(stream,64)!=payload:raise RuntimeError('echo integrity mismatch')
    return (time.monotonic()-started)*1000


def finish_idle(streams):
    def finish(item):
        elapsed=exchange(item,2)
        item.sendall(END)
        if exact(item,64)!=END:raise RuntimeError('persistent final frame mismatch')
        return elapsed
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(finish,streams))


def run_mixed(endpoint, logical_clients):
    if logical_clients not in (30,50,100):raise ValueError('unregistered cohort')
    held=[];lock=threading.Lock();stop=threading.Event()
    result={'logical_clients':logical_clients,'idle_connections':logical_clients-11,
            'baseline':{'message':[],'new_connection':[]},'loaded':{'message':[],'new_connection':[]},
            'bulk':[],'errors':[],'new_connections_completed':0,'complete':False}
    origin=time.monotonic();phase='idle_setup'
    def connect(index):
        sock=socket.create_connection(('127.0.0.1',endpoint.front),timeout=8)
        with lock:held.append(sock)
        sock.settimeout(25)
        domain=tuple(SECRETS)[index%2]
        stream=mtproxy_connect(sock,domain,SECRETS[domain])
        stream.sendall(struct.pack('!cQQ',b'e',64,128))
        exchange(stream,0)
        return stream
    def error(exc):
        result['errors'].append({'type':type(exc).__name__,'phase':phase,'reason':sanitize_startup_log(str(exc))[:160]})
    def probes(phase,streams,bulk=None):
        def message(stream):
            for index in range(10 if phase=='baseline' else 100):
                if phase=='loaded' and stop.is_set():break
                active=sum(not f.done() for f in bulk) if bulk else 0
                start=time.monotonic()
                elapsed=exchange(stream,index+1+(0 if phase=='baseline' else 20))
                result[phase]['message'].append({'offset_seconds':start-origin,'elapsed_ms':elapsed,'active_bulk':active})
                if phase=='loaded':stop.wait(.1)
                else:time.sleep(.05)
        def new_connection(worker):
            for _ in range(5 if phase=='baseline' else 100):
                if phase=='loaded' and stop.is_set():break
                active=sum(not f.done() for f in bulk) if bulk else 0
                start=time.monotonic()
                row=FullLab.transfer(endpoint,('apple.com','gs.apple.com')[worker%2],
                    'D',1024,125000000,persistent=True,io_timeout=25)
                with lock:result['new_connections_completed']+=1
                result[phase]['new_connection'].append({'offset_seconds':start-origin,
                    'elapsed_ms':row['elapsed_seconds']*1000,'fake_tls_ms':row['handshake_ms'],
                    'active_bulk':active,'sha256_verified':row['sha256_verified']})
                if phase=='loaded':stop.wait(.1)
                else:time.sleep(.05)
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            jobs=[pool.submit(message,stream) for stream in streams]+[pool.submit(new_connection,i) for i in range(4)]
            for job in jobs:
                try:job.result(timeout=100)
                except Exception as exc:error(exc);stop.set()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            idle=list(pool.map(connect,range(logical_clients-11)))
        streams=[connect(logical_clients+i) for i in range(2)]
        def keepalive():
            sequence=1
            while not stop.wait(5):
                try:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                        list(pool.map(lambda item:exchange(item,sequence),idle))
                    sequence+=1
                except Exception as exc:error(exc);stop.set();break
        keeper=threading.Thread(target=keepalive,name='synthetic-idle-keepalive');keeper.start()
        result['setup_seconds']=time.monotonic()-origin
        phase='baseline'
        probes('baseline',streams)
        if result['errors']:raise RuntimeError('baseline probes failed')
        def bulk_transfer(index):
            start=time.monotonic()
            row=FullLab.transfer(endpoint,tuple(SECRETS)[index%2],'D' if index<4 else 'U',PAYLOAD,
                                 125000000,persistent=True,io_timeout=25)
            row.update(start_offset_seconds=start-origin,end_offset_seconds=time.monotonic()-origin)
            return row
        phase='loaded'
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            jobs=[pool.submit(bulk_transfer,i) for i in range(5)]
            probe=pool.submit(probes,'loaded',streams,jobs)
            for job in jobs:
                try:result['bulk'].append(job.result(timeout=100))
                except Exception as exc:error(exc)
            stop.set();probe.result(timeout=30)
        keeper.join(timeout=30)
        if keeper.is_alive():raise RuntimeError('keepalive worker did not finish')
        phase='idle_recovery_and_close'
        # Serial teardown at 200ms can spend >35s walking 100 sockets and
        # violate the DC fixture's 25s idle timeout. Validate and finish each
        # connection together, bounded in parallel; do not blame that EOF on
        # a core or lengthen the production/fixture idle timeout.
        result['idle_recovery_ms']=finish_idle(idle)
        finish_idle(streams)
        result['dc_connections']=len(idle)+2+5+result['new_connections_completed']
        result['complete']=not result['errors'] and len(result['bulk'])==5
    except Exception as exc:error(exc)
    finally:
        stop.set()
        for sock in held:sock.close()
        if 'keeper' in locals():keeper.join(timeout=30)
        result['elapsed_seconds']=time.monotonic()-origin
    return result
