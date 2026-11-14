"""Measure borrowing and return of capacity on two owned WireGuard tunnels."""
import concurrent.futures
from collections import defaultdict
import json
import math
import os
from pathlib import Path
import re
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

from core_install import archive_payload
from dual_queue_lab import (BACKENDS, DOMAINS, LINKS, run_cohort, second_tunnel,
                            shape_backends_and_entry, shape_endpoint, start_frontend)
from gigabit_lab import set_tcp_limits
from kernel_observer import observe_sockets, private_namespace, qdiscs_in_namespace
from long_path_lab import Child
from mixed_experience_analysis import quantiles
from network_lab import Network, NAMES, command
from paired_performance import resources, write
from release_pins import TLS_BASELINE
from stable_acceptance import stats
from tunnel_budget import Budget, RESERVE, SHARE, SPLIT, TOTAL


def counters(endpoint):
    ns, dev = endpoint
    raw = command('ip','netns','exec',ns,'tc','-s','qdisc','show','dev',dev)
    leaf = re.search(r'^qdisc fq_codel 10: parent 1:10 .*?(?=^qdisc |\Z)',raw,re.M|re.S)
    if not leaf:
        raise RuntimeError('private fq_codel leaf disappeared')
    sent = re.search(r'^\s*Sent (\d+) bytes',leaf[0],re.M)
    backlog = re.search(r'^\s*backlog \S+ (\d+)p',leaf[0],re.M)
    if not sent or not backlog:
        raise RuntimeError('missing queue counters')
    return int(sent[1]), int(backlog[1])


class QueueSampler:
    def __init__(self, senders, adaptive):
        self.senders = senders
        self.adaptive = adaptive
        self.policy = Budget()
        self.sent = [counters(ep)[0] for ep in senders]
        self.started = self.last = time.monotonic()
        self.history = []

    def sample(self):
        sent, queued = zip(*(counters(ep) for ep in self.senders),strict=True)
        now = time.monotonic()
        if any(new<old for new,old in zip(sent,self.sent,strict=True)):
            raise RuntimeError('queue byte counter reset')
        rates = tuple((new-old)*8/1e6/(now-self.last) for new,old in zip(sent,self.sent,strict=True))
        self.sent, self.last = sent, now
        if self.adaptive:
            for index,rate in self.policy.updates(self.policy.sample(rates,queued)):
                shape_endpoint(*self.senders[index],rate)
                current = list(self.policy.rates)
                current[index] = rate
                self.policy.rates = tuple(current)
        self.history.append({'seconds':now-self.started,'rates_mbps':self.policy.rates,
                             'observed_mbps':rates,'queued_packets':queued})


def receive(namespace, peer, cpu, seconds, delay, start):
    time.sleep(max(0,start+delay-time.monotonic()))
    launched = time.monotonic()-start
    raw = json.loads(command('ip','netns','exec',namespace,'taskset','-c',str(cpu),
        'iperf3','-c',peer,'-R','-P','4','-J','-t',str(seconds),'--connect-timeout','3000',
        timeout=seconds+12))
    if raw.get('error') or raw['start']['test_start']['reverse']!=1:
        raise RuntimeError('receiver-side capacity measurement failed')
    intervals = []
    for item in raw['intervals']:
        row = item['sum']
        if row['omitted'] or row['sender'] or not .8<=row['seconds']<=1.2:
            continue
        if not math.isclose(row['bits_per_second'],row['bytes']*8/row['seconds'],rel_tol=.001):
            raise RuntimeError('receiver byte counter inconsistent')
        intervals.append({'start':launched+row['start'],'end':launched+row['end'],
                          'mbps':row['bits_per_second']/1e6})
    return {'launch_seconds':launched,'intervals':intervals,'raw':raw}


def run_trial(direction, adaptive, cpus):
    senders = LINKS[:2] if direction=='U' else LINKS[2:]
    recipients = ((BACKENDS[0],'10.77.1.1',cpus[1]), (BACKENDS[1],'10.77.2.1',cpus[2])) if direction=='U' else (
        (NAMES[0],'10.77.1.2',cpus[0]), (NAMES[0],'10.77.2.2',cpus[3]))
    for ns,dev in senders:
        shape_endpoint(ns,dev,SHARE)
    sampler = QueueSampler(senders,adaptive)
    started = sampler.started
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(receive,*receiver,duration,delay,started)
                for receiver,duration,delay in zip(recipients,(24,10),(0,7),strict=True)]
        while not all(job.done() for job in jobs):
            time.sleep(.25)
            sampler.sample()
        results = [job.result() for job in jobs]
    return {'direction':direction,'adaptive':adaptive,'timeline':sampler.history,'receivers':results}


def summarize_trial(trial):
    first, second = (r['intervals'] for r in trial['receivers'])
    windows = ((3,6.5),(9,16),(20,23.9))

    def median(rows, window):
        values = [r['mbps'] for r in rows if window[0]<=r['start'] and r['end']<=window[1]]
        if len(values)<2:
            raise ValueError('receiver window has fewer than two complete intervals')
        return statistics.median(values)

    return {'early':median(first,windows[0]),'overlap_first':median(first,windows[1]),
            'overlap_second':median(second,windows[1]),'late':median(first,windows[2])}


def assess(trials):
    result = []
    for direction in ('U','D'):
        fixed, adaptive = [next(r for r in trials if r['direction']==direction and r['adaptive']==mode)
                           for mode in (False,True)]
        old, new = map(summarize_trial,(fixed,adaptive))
        timeline = adaptive['timeline']
        borrowed = (TOTAL-RESERVE,RESERVE)
        second_launched = adaptive['receivers'][1]['launch_seconds']
        first_borrow = next((s['seconds'] for s in timeline if tuple(s['rates_mbps'])==borrowed),None)
        returned = next((s['seconds'] for s in timeline if s['seconds']>=second_launched
                         and tuple(s['rates_mbps'])==SPLIT),None)
        borrowed_again = next((s['seconds'] for s in timeline if s['seconds']>=18
                               and tuple(s['rates_mbps'])==borrowed),None)
        checks = {
            'idle_gain':new['early']>=1.20*old['early'] and new['late']>=1.20*old['late'],
            'both_active':all(new[k]>=.90*old[k] for k in ('overlap_first','overlap_second')),
            'borrow_return_borrow':first_borrow is not None and first_borrow<6
                                   and returned is not None and returned-second_launched<=2
                                   and borrowed_again is not None and borrowed_again<=20,
            'budget':all(sum(s['rates_mbps'])==TOTAL for s in timeline),
        }
        result.append({'direction':direction,'fixed_mbps':old,'adaptive_mbps':new,'checks':checks,
                       'first_borrow_seconds':first_borrow,'return_after_receiver_launch_seconds':
                       None if returned is None else returned-second_launched,
                       'borrowed_again_seconds':borrowed_again})
    return result


def proxy_trial(clients, dc, adaptive, *, solo):
    shape_backends_and_entry((SHARE,)*4)
    samplers = [QueueSampler(endpoints,adaptive) for endpoints in (LINKS[:2],LINKS[2:])]

    def observe(futures):
        while not all(f.done() for f in futures):
            time.sleep(.25)
            for sampler in samplers:
                sampler.sample()

    result = run_cohort(clients[:1] if solo else clients,dc,failed_over=False,
                        domains=DOMAINS[:1] if solo else DOMAINS,
                        payload_bytes=128*1024**2,observe=observe)
    return {'adaptive':adaptive,'solo':solo,'result':result,
            'timelines':[s.history for s in samplers]}


def compare_proxy(trials):
    checks = []
    for solo in (True,False):
        fixed, adaptive = [next(t for t in trials if t['solo']==solo and t['adaptive']==mode)
                           for mode in (False,True)]
        old, new = fixed['result'], adaptive['result']
        ratios = {d:new['rates_mbps'][d]/old['rates_mbps'][d] for d in ('D','U')}
        probes = [
            {'name':name,'samples':min(a[name]['loaded']['n'],b[name]['loaded']['n']),
             'added_ms':b[name]['loaded']['p95']-b[name]['idle']['p95'],
             'loaded_ratio':b[name]['loaded']['p95']/a[name]['loaded']['p95']}
            for a,b in zip(old['loaded'],new['loaded'],strict=True) for name in ('message','new_connection')]
        checks.append({
            'solo':solo,'ratios':ratios,'probes':probes,
            'passed':min(ratios.values())>=.95 and (not solo or max(ratios.values())>=1.20)
                     and (solo or min(old['bulk_overlap_seconds'],new['bulk_overlap_seconds'])>=1)
                     and all(p['samples']>=20 and p['added_ms']<=100 and p['loaded_ratio']<=1.10 for p in probes),
        })
    return checks


def diagnose_proxy(front, clients, cores, dc):
    samples = []
    pids = {'entry': front.pid, **{f'client{i}': c.process.pid for i,c in enumerate(clients,1)},
            **{f'dc{i}': c.process.pid for i,c in enumerate(dc,1)},
            **{f'core{i}': c.ready['pid'] for i,c in enumerate(cores,1)}}
    previous = {role: stats(pid)['cpu_seconds'] for role,pid in pids.items()}
    previous_time = time.monotonic()

    def observe(futures):
        nonlocal previous, previous_time
        while not all(f.done() for f in futures):
            time.sleep(.25)
            now = time.monotonic()
            current = {role: stats(pid)['cpu_seconds'] for role,pid in pids.items()}
            with private_namespace(NAMES[0]):
                entry = observe_sockets(front.pid,24443,frontend=True)
            core_sockets = [core.call({'command':'observe'}) for core in cores]
            qdiscs = {ns: qdiscs_in_namespace(ns) for ns in (*NAMES,BACKENDS[1])}
            samples.append({'monotonic_seconds':now, 'observation_seconds':time.monotonic()-now,
                            'entry_sockets':entry, 'core_sockets':core_sockets, 'qdiscs':qdiscs,
                            'cpu_percent_one_core': {role:100*(current[role]-old)/(now-previous_time)
                                                     for role,old in previous.items()}})
            previous, previous_time = current, now

    result = run_cohort(clients,dc,failed_over=False,payload_bytes=128*1024**2,
                        observe=observe,trace_timing=True)
    return {'result':result, 'samples':samples}


def connection_phases(trial):
    by_port = defaultdict(list)
    for line in trial['haproxy_log']:
        _, port, seconds, millis, backend, server, wait, connect, retries, termination = line.split()
        accepted_at = int(seconds)*1000 + int(millis)
        by_port[int(port)].append((accepted_at,backend,server,int(wait),int(connect),retries,termination))
    results = []
    for line_index, row in enumerate(trial['result']['rows']):
        expected = (('line_a','first'),('line_b','second'))[line_index]
        line = {}
        for phase in ('baseline','loaded'):
            probes = []
            for sample in row[phase]['new_connection']:
                if phase == 'loaded' and sample['active_bulk'] == 0:
                    continue
                timing = sample['phase_timing']
                stages = {name:value for name,value in timing.items() if name.endswith('_ms')
                          and name != 'started_epoch_ms'}
                start = timing['started_epoch_ms']
                ready = start + stages['local_connect_ms'] + stages['to_transport_ready_ms']
                # The TCP connect syscall can complete well before HAProxy accepts the socket.
                # Its accept and backend connection must precede client-side transport readiness.
                candidates = [item for item in by_port[timing['client_port']]
                              if start-2 <= item[0] and item[0]+item[3]+item[4] <= ready+2]
                if len(candidates) != 1:
                    raise RuntimeError(f'expected one HAProxy session in probe accept window, found {len(candidates)}')
                accepted_at, backend, server, wait, connect, retries, termination = candidates[0]
                if ((backend,server) != expected or min(wait,connect,int(retries)) < 0
                        or not sample['sha256_verified'] or min(stages.values()) < 0
                        or sum(stages.values()) > sample['elapsed_ms'] + .01):
                    raise RuntimeError('connection phase timing or backend join mismatch')
                accept_delay = max(0, accepted_at-start-stages['local_connect_ms'])
                residual = stages['to_transport_ready_ms'] - accept_delay - wait - connect
                if residual < -2:
                    raise RuntimeError('HAProxy timings exceed client transport phase')
                probes.append(dict(stages, frontend_accept_delay_ms=accept_delay,
                                   backend_queue_ms=wait, backend_connect_ms=connect,
                                   transport_after_backend_ms=max(0,residual), elapsed_ms=sample['elapsed_ms']))
                sample['haproxy'] = {'backend':backend, 'server':server, 'accepted_epoch_ms':accepted_at, 'queue_ms':wait,
                                     'connect_ms':connect, 'retries':int(retries), 'termination':termination}
            if len(probes) < 20:
                raise RuntimeError('insufficient new connection phase timings')
            slowest = sorted(probes,key=lambda row:row['elapsed_ms'],reverse=True)[:math.ceil(len(probes)/10)]
            line[phase] = {'quantiles':{name:quantiles([row[name] for row in probes]) for name in probes[0]},
                           'slowest_decile_medians_ms':{name:statistics.median(row[name] for row in slowest)
                                                        for name in slowest[0]}}
        results.append(line)
    return results


def proxy_measure(net, cpus, report, output, archive, children, *, diagnose, affinity_control):
    binary = net.root/'balanced2'
    payload,_ = archive_payload(archive,TLS_BASELINE[0],TLS_BASELINE[2].removeprefix('telemt '),TLS_BASELINE[1],False)
    binary.write_bytes(payload)
    binary.chmod(0o755)
    del payload
    report['binary_sha256'] = TLS_BASELINE[1]
    for ns,address in zip(BACKENDS,('198.18.0.1','198.18.0.5'),strict=True):
        command('ip','-n',ns,'address','add',address+'/32','dev','lo')
    dc = [Child('dc',ns,cpus[3],{'mixed':True},children) for ns in BACKENDS]
    clients = [Child('client',NAMES[0],cpus[0],{},children) for _ in DOMAINS]
    front = start_frontend(net, connection_timings=diagnose)
    os.sched_setaffinity(front.pid,{cpus[0]})
    cores = [Child('core',ns,cpu,{'kind':'balanced2','binary':str(binary),
                                'synthetic_tls_front':True},children)
             for ns,cpu in zip(BACKENDS,cpus[1:3],strict=True)]
    if diagnose:
        log = net.root/'haproxy-timings.log'
        arms = (('shared-client',cpus[0]), ('shared-dc',cpus[3]), ('shared-client',cpus[0])) if affinity_control else (
            ('shared-client',cpus[0]),)
        for arm,cpu in arms:
            for task in Path(f'/proc/{front.pid}/task').iterdir():
                os.sched_setaffinity(int(task.name),{cpu})
            with log.open('r',encoding='ascii') as source:
                source.seek(0,os.SEEK_END)
                trial = diagnose_proxy(front,clients,cores,dc)
                trial.update(arm=arm, entry_cpu=cpu,
                             haproxy_log=[line.rstrip('\n') for line in source if line.startswith('tdh ')])
            report['trials'].append(trial)
            write(output,report)
            if trial['result']['bulk_overlap_seconds'] < 1 or not trial['samples']:
                raise RuntimeError('insufficient concurrent load observation')
            trial['connection_phases'] = connection_phases(trial)
            write(output,report)
        return
    for solo,modes in ((True,(False,True)),(False,(True,False))):
        for mode in modes:
            report['trials'].append(proxy_trial(clients,dc,mode,solo=solo))
            write(output,report)
    report['comparison'] = compare_proxy(report['trials'])


def main(output, archive=None, *, diagnose=False, affinity_control=False):
    if diagnose and archive is None:
        raise ValueError('proxy diagnostics require the pinned balanced2 archive')
    if os.geteuid()!=0 or os.environ.get('GITHUB_ACTIONS')!='true':
        raise RuntimeError('disposable CI only')
    before = resources()
    if (before['cpu_max'],before['memory_max'],before['swap_max'])!=('400000 100000',str(2048*1024**2),'0'):
        raise RuntimeError('unexpected CI limits')
    cpus = sorted(os.sched_getaffinity(0))[:4]
    if len(cpus)!=4:
        raise RuntimeError('four logical CPUs required')
    report = {'source_commit':os.environ['TDH_SOURCE_COMMIT'],'trials':[],'complete':False,
              'proxy':archive is not None, 'diagnose':diagnose, 'affinity_control':affinity_control}
    children = []
    net = None
    with tempfile.TemporaryDirectory(prefix='tdh-audit-tunnel-budget-') as folder:
        try:
            net = Network(SimpleNamespace(root=folder,wg='wg',iperf='iperf3',start_iperf=False,
                rtt_ms=100,rate_mbps=1000,loss_percent=0,mtu=1420,queue_limit_packets=50000))
            net.setup()
            second_tunnel(net)
            command('ip','netns','exec',NAMES[0],'tc','qdisc','replace','dev','tdh-la0','root',
                    'tbf','rate','1000mbit','burst','256kb','limit','31250000')
            report['tcp_limits'] = set_tcp_limits((NAMES[0],*BACKENDS),'larger_max',{})
            shape_backends_and_entry((SHARE,)*4,install=True)
            if archive:
                proxy_measure(net,cpus,report,output,archive,children,diagnose=diagnose,
                              affinity_control=affinity_control)
            else:
                servers = ((NAMES[0],'10.77.1.1',cpus[0]),(NAMES[0],'10.77.2.1',cpus[3]),
                           (BACKENDS[0],'10.77.1.2',cpus[1]),(BACKENDS[1],'10.77.2.2',cpus[2]))
                for ns,address,cpu in servers:
                    net.processes.append(subprocess.Popen(['ip','netns','exec',ns,'taskset','-c',str(cpu),
                        'iperf3','-s','-B',address],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
                time.sleep(.5)
                if any(p.poll() is not None for p in net.processes):
                    raise RuntimeError('private capacity server failed to start')
                for direction,modes in (('U',(False,True)),('D',(True,False))):
                    for mode in modes:
                        report['trials'].append(run_trial(direction,mode,cpus))
                        write(output,report)
                report['comparison'] = assess(report['trials'])
            report['complete'] = True
        except (Exception,KeyboardInterrupt) as exc:
            report['failure'] = {'type':type(exc).__name__,'detail':str(exc)[:800]}
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
            oom = {k:after['memory_events'][k]-before['memory_events'][k] for k in ('oom','oom_kill')}
            report.update(resources_after=after,cleanup_errors=errors,oom_delta=oom,
                          accepted=report['complete'] and not errors and not any(oom.values()) and after['pids_current']==1)
            if not diagnose:
                report['screen_passed'] = report['accepted'] and all(
                    p['passed'] if archive else all(p['checks'].values()) for p in report['comparison'])
            write(output,report)
    return 0 if report['accepted' if diagnose else 'screen_passed'] else 1


if __name__=='__main__':
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    mode = sys.argv[3:]
    if mode not in ([],['--diagnose'],['--affinity-control']):
        raise SystemExit('unsupported tunnel budget mode')
    raise SystemExit(main(Path(sys.argv[1]),Path(sys.argv[2]) if len(sys.argv)>2 else None,
                          diagnose=bool(mode),affinity_control=mode == ['--affinity-control']))
