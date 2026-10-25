"""Low-traffic persistent upload/download through the complete private path."""
import concurrent.futures
import json
from pathlib import Path
import socket
import struct
import threading
import time

from lab import exact,send_payload,receive_payload
from mtproto_transport import mtproxy_connect
from telemt_probe import SECRETS
from endurance_contract import RATE,PAYLOAD_MAX,schedule,validate
from candidate_contract import validate_wireguard
from network_lab import command


def run(seconds,shared,root,counts,wg,save):
    plan=schedule(seconds)
    result={'seconds_requested':seconds,'complete':False,'rows':[],
            'classification':'PRIVATE_LOW_TRAFFIC_ENDURANCE','payload_max_bytes':PAYLOAD_MAX,
            'production_accessed':False,'throughput_or_capacity_claimed':False,'samples':[]}
    output=root/'results/endurance.json'
    sockets=set();lock=threading.Lock();pool=concurrent.futures.ThreadPoolExecutor(max_workers=2)
    def states():
        values=[json.loads((shared/f'backend{i}.json').read_text()) for i in (1,2)]
        assert all(v['ready'] and v['telemt']=='telemt 3.5.5+tdh.balanced2' and not v['errors'] and
                   not v['socket_observation_errors'] for v in values), 'backend health or identity'
        return values
    def identity(values): return [(v['pid'],v['start_ticks']) for v in values]
    def counters():
        totals={}
        for device in ('wg-lab','wg-lab2'):
            records=[line.split() for line in command(wg,'show',device,'transfer').splitlines()]
            assert records and all(len(row)==3 for row in records)
            totals[device]={'rx':sum(int(row[1]) for row in records),'tx':sum(int(row[2]) for row in records)}
        return totals
    def transfer(domain,direction,size,rate,kind,persistent=False):
        started=time.monotonic()
        with socket.create_connection(('127.0.0.1',24433),timeout=8) as sock:
            sock.settimeout(8)
            with lock:sockets.add(sock)
            try:
                stream=mtproxy_connect(sock,domain,SECRETS[domain],fragment=True)
                stream.sendall(struct.pack('!cQQ',(direction.lower() if persistent else direction).encode(),size,rate))
                digest=receive_payload(stream,size) if direction=='D' else send_payload(stream,size,rate)
                if persistent and direction=='D':stream.sendall(b'ACK!')
                assert exact(stream,32)==digest,'payload digest mismatch'
                if persistent and direction=='U':
                    stream.sendall(b'ACK!');assert exact(stream,2)==b'OK'
            finally:
                with lock:sockets.discard(sock)
        return {'kind':kind,'domain_index':list(SECRETS).index(domain)+1,'direction':direction,
                'backend_index':list(SECRETS).index(domain)+1,'bytes':size,'sha256_verified':True,
                'persistent_dc':persistent,'elapsed_seconds':time.monotonic()-started}
    def routed(domain,direction,size,rate,kind):
        before=counts()
        row=transfer(domain,direction,size,rate,kind)
        delta=[b-a for a,b in zip(before,counts())]
        expected=[1,0] if domain=='apple.com' else [0,1]
        assert delta==expected,'short flow route changed'
        result['rows'].append(row)
    futures=[]
    try:
        baseline=states();before_wg=counters();before=counts()
        started=time.monotonic()
        futures=[pool.submit(transfer,domain,direction,seconds*RATE,RATE,'long',True)
                 for domain,direction in (('apple.com','D'),('gs.apple.com','U'))]
        deadline=time.monotonic()+15
        while counts()!=[before[0]+1,before[1]+1]:
            for future in futures:
                if future.done():future.result();raise RuntimeError('long flow ended during setup')
            assert time.monotonic()<deadline,'persistent flow assignment deadline'
            time.sleep(.1)
        result['initial_route_delta']=[b-a for a,b in zip(before,counts())]
        for minute in range(plan['probes']):
            target=started+minute*60
            while time.monotonic()<target:
                for future in futures:
                    if future.done():future.result();raise RuntimeError('persistent flow ended early')
                time.sleep(min(1,target-time.monotonic()))
            current=states()
            assert identity(current)==identity(baseline),'backend restarted'
            assert all(v['fd_count']<=b['fd_count']+64 for v,b in zip(current,baseline)), 'descriptor growth cap'
            if minute%60==0:
                for domain in ('apple.com','gs.apple.com'):
                    for direction in ('D','U'):routed(domain,direction,131072,131072,'file')
            routed(('apple.com','gs.apple.com')[minute%2],'U',1024,131072,'probe')
            result['samples'].append({'elapsed_seconds':time.monotonic()-started,
                                     'backend_fd_count':[v['fd_count'] for v in current]})
            save(output,result)
        for future in futures:result['rows'].append(future.result(timeout=75))
        # The DC and backend state exporters intentionally sample once per 5s.
        time.sleep(11)
        current=states()
        result['backend_identity_unchanged']=identity(current)==identity(baseline)
        result['backend_errors']=[v['errors'] for v in current]
        assert sum(v['completed'] for v in current)==len(result['rows']), 'DC completion accounting'
        observed=[]
        for state in current:
            values=[p['rtt_ms'] for p in state['client_sockets'] if isinstance(p.get('rtt_ms'),(int,float))]
            assert values and max(values)>5,'high RTT client hop unobserved'
            observed.append({'samples':len(values),'min_rtt_ms':min(values),'max_rtt_ms':max(values)})
        after=counters()
        result['wireguard_transfer_delta_bytes']={name:{key:after[name][key]-before_wg[name][key] for key in ('rx','tx')} for name in before_wg}
        validate_wireguard(result['rows'],result['wireguard_transfer_delta_bytes'])
        result.update(complete=True,observed_client_tcp=observed,payload_bytes=sum(r['bytes'] for r in result['rows']))
        validate(result);save(output,result)
    except BaseException as error:
        result.update(complete=False,error_type=type(error).__name__)
        save(output,result)
        raise
    finally:
        with lock:
            for sock in sockets:
                try:sock.shutdown(socket.SHUT_RDWR)
                except OSError:pass
        pool.shutdown(wait=True,cancel_futures=True)
