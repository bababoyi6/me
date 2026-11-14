"""Shared-bottleneck, dual-backend queue experiment in owned CI namespaces."""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import time
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

from long_path_lab import Child
from mixed_experience_analysis import quantiles
from network_lab import Network, NAMES, command
from paired_performance import resources, write
from private_shaper_analysis import TLS_CANDIDATE_SHA
from stable_acceptance import stats
from tls_candidate_lab import candidate_binary


BACKENDS = (NAMES[2], 'tdh-audit-b2')
DOMAINS = ('apple.com', 'gs.apple.com')
LINKS = ((NAMES[0], 'wg-lab'), (NAMES[0], 'wg-lab2'),
         (BACKENDS[0], 'wg-lab'), (BACKENDS[1], 'wg-lab'))


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
        'limit',str(getattr(net.args,'queue_limit_packets',10000)),
        'delay',f'{net.args.rtt_ms/2:g}ms','rate',f'{net.args.rate_mbps}mbit')
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
        command('ip','-n',where,'link','set',dev,'mtu','1420')
    # Bring up the listening endpoint before the initiating entry. Otherwise
    # PersistentKeepalive can send the initial handshake while B2 does not yet
    # exist, and a short readiness ping expires inside WG's 5-second retry.
    command('ip','-n',ns,'link','set','wg-lab','up')
    command('ip','-n',NAMES[0],'link','set','wg-lab2','up')
    command('ip','netns','exec',NAMES[0],'ping','-n','-c','2','-W','2','10.77.2.2',timeout=8)
    command('ip','netns','exec',ns,'tc','qdisc','replace','dev','tdh-lc1','root','fq')


def shape_backends_and_entry(rates, *, install=False):
    """Use one private queue per tunnel endpoint. Update the class in place."""
    for (namespace, device), rate in zip(LINKS, rates, strict=True):
        shape_endpoint(namespace, device, rate, install=install)


def shape_endpoint(namespace, device, rate, *, install=False):
    if (namespace, device) not in LINKS:
        raise ValueError('expected a private tunnel endpoint')
    prefix = ('ip', 'netns', 'exec', namespace, 'tc')
    if install:
        command('ip', '-n', namespace, 'link', 'set', device, 'txqueuelen', '1000')
        command(*prefix, 'qdisc', 'add', 'dev', device, 'root', 'handle', '1:', 'htb', 'default', '10')
    command(*prefix, 'class', 'add' if install else 'change', 'dev', device,
            'parent', '1:', 'classid', '1:10', 'htb', 'rate', f'{rate}mbit',
            'ceil', f'{rate}mbit', 'quantum', '1514')
    if install:
        command(*prefix, 'qdisc', 'add', 'dev', device, 'parent', '1:10', 'handle', '10:', 'fq_codel')
    observed = command(*prefix, '-d', 'class', 'show', 'dev', device)
    if f'rate {rate}Mbit ceil {rate}Mbit' not in observed:
        raise RuntimeError('queue rate differs from requested budget')


def snapshot():
    return {f'{ns}/{dev}': {
        'qdisc': command('ip','netns','exec',ns,'tc','-s','-d','qdisc','show','dev',dev),
        'class': command('ip','netns','exec',ns,'tc','-s','-d','class','show','dev',dev),
    } for ns, dev in LINKS}


def start_frontend(net, *, connection_timings=False):
    cfg = net.root/'haproxy.cfg'
    traffic_log = ('    log stdout format raw local0\n'
                   '    log-format "tdh %cp %b %s %Tw %Tc %rc %ts"\n') if connection_timings else ''
    cfg.write_text(f'''global
    nbthread 1
    stats socket {net.root}/haproxy.sock mode 600 level admin
defaults
    mode tcp
    timeout connect 3s
    timeout client 30s
    timeout server 30s
frontend lab
{traffic_log}    bind 127.0.0.1:24443
    tcp-request inspect-delay 2s
    tcp-request content accept if {{ req.ssl_hello_type 1 }}
    use_backend line_b if {{ req.ssl_sni -i gs.apple.com }}
    default_backend line_a
backend line_a
    server first 10.77.1.2:24431 send-proxy-v2
    server second 10.77.2.2:24431 send-proxy-v2 backup
backend line_b
    server second 10.77.2.2:24431 send-proxy-v2
    server first 10.77.1.2:24431 send-proxy-v2 backup
''', encoding='ascii')
    args = ['ip','netns','exec',NAMES[0],'haproxy','-db','-f',str(cfg)]
    if connection_timings:
        with (net.root/'haproxy-timings.log').open('wb') as log:
            process = subprocess.Popen(args,stdout=log,stderr=subprocess.DEVNULL)
    else:
        process = subprocess.Popen(args,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    net.processes.append(process)
    for _ in range(30):
        if process.poll() is not None:
            raise RuntimeError('private HAProxy exited')
        if (net.root/'haproxy.sock').is_socket():
            return process
        time.sleep(.1)
    raise RuntimeError('private HAProxy readiness timeout')


def set_second_route(root, enabled):
    command_name = 'enable' if enabled else 'disable'
    for backend in ('line_a','line_b'):
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(3)
            sock.connect(str(root/'haproxy.sock'))
            sock.sendall(f'{command_name} server {backend}/second\n'.encode('ascii'))
            sock.shutdown(socket.SHUT_WR)
            if sock.recv(4096).strip():
                raise RuntimeError('HAProxy admin command rejected')


def run_cohort(clients, dc, *, failed_over, payload_bytes=32*1024**2, observe=None, domains=DOMAINS,
               trace_timing=False):
    before = [w.call({'command':'stats'}) for w in dc]
    with ThreadPoolExecutor(max_workers=2) as executor:
        def workload(pair):
            client, domain = pair
            client.send({'command':'mixed','logical_clients':30,'candidate_screen':True,
                         'domain':domain,'payload_bytes':payload_bytes,'trace_timing':trace_timing})
            return client.receive(110)
        futures = [executor.submit(workload,pair) for pair in zip(clients,domains,strict=True)]
        if observe:
            observe(futures)
        rows = [future.result() for future in futures]
    if any(row.get('errors') or not row.get('complete') for row in rows):
        raise RuntimeError('mixed workload failed: '+json.dumps([row['errors'] for row in rows]))
    expected = [0,0]
    for domain,row in zip(domains,rows,strict=True):
        expected[0 if failed_over else DOMAINS.index(domain)] += row['dc_connections']
    for _ in range(10):
        after = [w.call({'command':'stats'}) for w in dc]
        counts = [new['completed']-old['completed'] for old,new in zip(before, after, strict=True)]
        if counts == expected or any(actual>target for actual,target in zip(counts,expected,strict=True)):
            break
        time.sleep(.05)
    if any(x['errors'] for x in before+after):
        raise RuntimeError('mock DC protocol error: '+json.dumps([x['errors'] for x in after]))
    if counts != expected:
        raise RuntimeError(f'SNI backend session counts differ: {counts} vs {expected}')
    all_bulk = []
    windows = []
    summaries = []
    for row in rows:
        origin = row['monotonic_origin_seconds']
        flows = row['bulk']
        if sorted(f['direction'] for f in flows) != ['D']*4+['U'] or any(
                f['bytes'] != payload_bytes or not f['sha256_verified'] or not f['persistent_dc'] for f in flows):
            raise RuntimeError('file payload or integrity mismatch')
        all_bulk.extend((f['direction'],f['bytes'],origin+f['start_offset_seconds'],
                         origin+f['end_offset_seconds']) for f in flows)
        windows.append((origin+min(f['start_offset_seconds'] for f in flows),
                        origin+max(f['end_offset_seconds'] for f in flows)))
        summaries.append({kind: {
            'loaded': quantiles([s['elapsed_ms'] for s in row['loaded'][kind] if s['active_bulk']>0]),
            'idle': quantiles([s['elapsed_ms'] for s in row['baseline'][kind]]),
        } for kind in ('message','new_connection')})
    rates = {}
    for direction in ('D','U'):
        flows = [f for f in all_bulk if f[0]==direction]
        rates[direction] = sum(f[1] for f in flows)*8/1e6/(max(f[3] for f in flows)-min(f[2] for f in flows))
    return {'failed_over':failed_over,'rows':rows,'loaded':summaries,'rates_mbps':rates,
            'backend_sessions':counts,
            'bulk_overlap_seconds': min(end for _,end in windows)-max(start for start,_ in windows)}


def assess(rows):
    original, split, narrow, wide = rows
    types = ('message','new_connection')
    pairs = [(a[kind],b[kind]) for a,b in zip(original['loaded'],split['loaded'],strict=True) for kind in types]
    return {
        'concurrent': all(row['bulk_overlap_seconds'] >= 1 for row in rows),
        'sample_coverage': all(route[kind]['loaded']['n'] >= 20
                               for row in rows for route in row['loaded'] for kind in types),
        'split_throughput': all(split['rates_mbps'][d] >= .95*original['rates_mbps'][d] for d in ('U','D')),
        'split_latency': all(new['loaded']['p95'] <= .90*old['loaded']['p95'] for old,new in pairs),
        'absolute_latency': all(route[kind]['loaded']['p95'] <= route[kind]['idle']['p95']+100
                                for row in (split,wide) for route in row['loaded'] for kind in types),
        'capacity_reclaimed': wide['rates_mbps']['D'] >= 1.30*narrow['rates_mbps']['D']
                              and wide['rates_mbps']['U'] >= .95*narrow['rates_mbps']['U'],
    }


def verify_route(client, dc, domain, index):
    prior = [worker.call({'command':'stats'}) for worker in dc]
    client.send({'command':'single','domain':domain})
    row = client.receive(15)
    for _ in range(10):
        after = [worker.call({'command':'stats'}) for worker in dc]
        counts = [new['completed']-old['completed'] for old,new in zip(prior,after,strict=True)]
        if any(counts):
            break
        time.sleep(.05)
    if (not row['sha256_verified'] or counts[index]!=1 or counts[1-index]!=0
            or any(state['errors'] for state in prior+after)):
        raise RuntimeError('synthetic file did not reach the expected backend')
    return {'target_backend':index+1,'backend_sessions':counts,'sha256_verified':True}


def restore_second_tunnel(report):
    started = time.monotonic()
    command('ip','-n',BACKENDS[1],'link','set','wg-lab','up')
    report['restore_probes'] = []
    deadline = started + 30
    while time.monotonic() < deadline:
        probe = subprocess.run(['ip','netns','exec',NAMES[0],'ping','-n','-c','1','-W','1','10.77.2.2'],
                               stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=3)
        elapsed = time.monotonic()-started
        report['restore_probes'].append({'elapsed_seconds':elapsed,'success':probe.returncode==0})
        if probe.returncode==0:
            report['wireguard_restored_seconds'] = elapsed
            return
        time.sleep(.5)
    raise RuntimeError('second WireGuard endpoint not reachable within 30 seconds')


def main(args):
    if os.geteuid()!=0 or os.environ.get('GITHUB_ACTIONS')!='true':
        raise RuntimeError('disposable CI only')
    before = resources()
    if (before['cpu_max'],before['memory_max'],before['swap_max']) != ('300000 100000',str(1536*1024**2),'0'):
        raise RuntimeError('unexpected CI resource limits')
    cpus = sorted(os.sched_getaffinity(0))
    if len(cpus)<4:
        raise RuntimeError('four available logical CPUs required for separate workers')
    os.sched_setaffinity(0,{cpus[0]})
    source = os.environ['TDH_SOURCE_COMMIT']
    binary, pin = candidate_binary(args.candidate,'new',source)
    if pin['sha256'] != TLS_CANDIDATE_SHA:
        raise RuntimeError('unexpected candidate binary digest')
    report = {'source_commit':source,'binary_sha256':pin['sha256'],'complete':False,'rows':[],
              'recovery_only':args.recovery_only,
              'resources_before':before}
    children = []
    net = None
    with tempfile.TemporaryDirectory(prefix='tdh-audit-dual-queue-') as folder:
        try:
            net = Network(SimpleNamespace(root=folder,wg='wg',iperf='iperf3',start_iperf=False,
                                          rtt_ms=100,rate_mbps=200,loss_percent=0,mtu=1420))
            net.setup()
            second_tunnel(net)
            # Both encrypted downlinks share the router's tdh-la1 netem.
            # Match that limit on entry upload, ahead of the two router branches.
            command('ip','netns','exec',NAMES[0],'tc','qdisc','replace','dev','tdh-la0',
                    'root','tbf','rate','200mbit','burst','64kb','limit','6250000')
            for ns, address in zip(BACKENDS, ('198.18.0.1','198.18.0.5'), strict=True):
                command('ip','-n',ns,'address','add',address+'/32','dev','lo')
            dc = [Child('dc',ns,cpus[3],{'mixed':True},children) for ns in BACKENDS]
            cores = [Child('core',ns,cpu,{'kind':'balanced','binary':str(binary),'synthetic_tls_front':True,
                                         'experimental_binary':[pin['sha256'],pin['version']]},children)
                     for ns,cpu in zip(BACKENDS,cpus[1:3],strict=True)]
            clients = [Child('client',NAMES[0],cpus[0],{},children) for _ in DOMAINS]
            start_frontend(net)
            initial_fd = [stats(w.ready['pid'])['fds'] for w in cores]
            if args.recovery_only:
                report['initial_routes'] = [verify_route(client,dc,domain,index)
                                            for index,(client,domain) in enumerate(zip(clients,DOMAINS,strict=True))]
            else:
                report['rows'].append(dict(policy='original', **run_cohort(clients,dc,failed_over=False)))
            shape_backends_and_entry((90,90,90,90),install=True)
            if not args.recovery_only:
                report['rows'].append(dict(policy='split90', queue_before=snapshot(),
                                           **run_cohort(clients,dc,failed_over=False),queue_after=snapshot()))
            set_second_route(net.root,False)
            command('ip','-n',BACKENDS[1],'link','set','wg-lab','down')
            if args.recovery_only:
                report['failover_route'] = verify_route(clients[1],dc,DOMAINS[1],0)
            else:
                report['rows'].append(dict(policy='survivor90', queue_before=snapshot(),
                                           **run_cohort(clients,dc,failed_over=True),queue_after=snapshot()))
            shape_backends_and_entry((180,90,180,90))
            if args.recovery_only:
                # Reproduce the previous down interval so cached peer state ages similarly.
                time.sleep(55)
            else:
                report['rows'].append(dict(policy='survivor180', queue_before=snapshot(),
                                           **run_cohort(clients,dc,failed_over=True),queue_after=snapshot()))
            shape_backends_and_entry((90,90,90,90))
            restore_second_tunnel(report)
            set_second_route(net.root,True)
            report['restored_routes'] = [verify_route(client,dc,domain,index)
                                         for index,(client,domain) in enumerate(zip(clients,DOMAINS,strict=True))]
            time.sleep(2)
            report['backend_fds'] = [stats(w.ready['pid'])['fds'] for w in cores]
            if any(now>old+2 for now,old in zip(report['backend_fds'],initial_fd,strict=True)):
                raise RuntimeError('backend descriptors failed to return to idle')
            report['primary_restored'] = True
            report['checks'] = {'primary_restored':True} if args.recovery_only else assess(report['rows'])
            report['complete'] = True
        except (Exception, KeyboardInterrupt) as exc:
            report['failure'] = {'type':type(exc).__name__,'detail':str(exc)[:1000]}
            raise
        finally:
            errors = []
            for child in reversed(children):
                try:
                    child.close()
                except Exception as exc:
                    errors.append(type(exc).__name__)
            if net:
                try:
                    net.close()
                except Exception as exc:
                    errors.append(type(exc).__name__)
            after = resources()
            report.update(resources_after=after,cleanup_errors=errors,
                          oom_delta={k:after['memory_events'][k]-before['memory_events'][k]
                                     for k in ('oom','oom_kill')})
            report['accepted'] = (report['complete'] and not errors and not any(report['oom_delta'].values())
                                  and after['pids_current']==1 and all(w.process.poll() is not None for w in children))
            report['screen_passed'] = report['accepted'] and all(report['checks'].values())
            write(args.output,report)
    return 0 if report['screen_passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--recovery-only', action='store_true')
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    raise SystemExit(main(parser.parse_args()))
