"""Measure borrowing and return of capacity on two owned WireGuard tunnels."""
import concurrent.futures
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

from dual_queue_lab import BACKENDS, LINKS, second_tunnel, shape_backends_and_entry, shape_endpoint
from gigabit_lab import set_tcp_limits
from network_lab import Network, NAMES, command
from paired_performance import resources, write
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
    policy = Budget()
    history = []
    previous = [counters(ep)[0] for ep in senders]
    started = last = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(receive,*receiver,duration,delay,started)
                for receiver,duration,delay in zip(recipients,(24,10),(0,7),strict=True)]
        while not all(job.done() for job in jobs):
            time.sleep(.25)
            current = [counters(ep) for ep in senders]
            now = time.monotonic()
            sent, queued = zip(*current,strict=True)
            if any(new<old for new,old in zip(sent,previous,strict=True)):
                raise RuntimeError('queue byte counter reset')
            rates = tuple((new-old)*8/1e6/(now-last) for new,old in zip(sent,previous,strict=True))
            previous, last = sent, now
            if adaptive:
                for index,rate in policy.updates(policy.sample(rates,queued)):
                    shape_endpoint(*senders[index],rate)
                    current_rates = list(policy.rates)
                    current_rates[index] = rate
                    policy.rates = tuple(current_rates)
            history.append({'seconds':now-started,'rates_mbps':policy.rates,
                            'observed_mbps':rates,'queued_packets':queued})
        results = [job.result() for job in jobs]
    return {'direction':direction,'adaptive':adaptive,'timeline':history,'receivers':results}


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


def main(output):
    if os.geteuid()!=0 or os.environ.get('GITHUB_ACTIONS')!='true':
        raise RuntimeError('disposable CI only')
    before = resources()
    if (before['cpu_max'],before['memory_max'],before['swap_max'])!=('400000 100000',str(2048*1024**2),'0'):
        raise RuntimeError('unexpected CI limits')
    cpus = sorted(os.sched_getaffinity(0))[:4]
    if len(cpus)!=4:
        raise RuntimeError('four logical CPUs required')
    report = {'source_commit':os.environ['TDH_SOURCE_COMMIT'],'trials':[],'complete':False}
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
            if net:
                try:
                    net.close()
                except Exception as exc:
                    errors.append(type(exc).__name__)
            after = resources()
            oom = {k:after['memory_events'][k]-before['memory_events'][k] for k in ('oom','oom_kill')}
            report.update(resources_after=after,cleanup_errors=errors,oom_delta=oom,
                          accepted=report['complete'] and not errors and not any(oom.values()) and after['pids_current']==1)
            report['screen_passed'] = report['accepted'] and all(
                all(p['checks'].values()) for p in report['comparison'])
            write(output,report)
    return 0 if report['screen_passed'] else 1


if __name__=='__main__':
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    raise SystemExit(main(Path(sys.argv[1])))
