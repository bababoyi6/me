"""Bounded comparison on a shared, synthetic 1 Gbit/s WireGuard path."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
from types import SimpleNamespace

from core_install import archive_payload
from dual_queue_lab import (BACKENDS, DOMAINS, LINKS, run_cohort, second_tunnel,
                            shape_backends_and_entry, snapshot, start_frontend)
from kernel_observer import qdiscs_in_namespace
from long_path_lab import Child
from network_lab import Network, NAMES, command
from paired_performance import resources, write
from path_capacity import validate
from private_shaper_analysis import TLS_CANDIDATE_SHA
from release_pins import TLS_BASELINE
from stable_acceptance import stats
from tls_candidate_lab import candidate_binary


FILE_BYTES = 128 * 1024**2
ARMS = ('balanced2', 'candidate', 'candidate-shaped')


def capacity(net, evidence, entry_cpu, backend_cpu):
    paths = {
        'tcp': ('192.0.2.1', '198.51.100.2'),
        'wireguard': ('10.77.1.1', '10.77.1.2'),
    }
    for addresses in paths.values():
        for ns, address, cpu in zip((NAMES[0], BACKENDS[0]), addresses, (entry_cpu, backend_cpu), strict=True):
            net.processes.append(subprocess.Popen(
                ['ip','netns','exec',ns,'taskset','-c',str(cpu),'iperf3','-s','-B',address,'-p','5201'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    time.sleep(.4)
    if any(proc.poll() is not None for proc in net.processes):
        raise RuntimeError('capacity server exited')
    for path, (entry, backend) in paths.items():
        for direction, receiver, sender, cpu in (
                ('D', NAMES[0], backend, entry_cpu), ('U', BACKENDS[0], entry, backend_cpu)):
            for streams in (1,4):
                row = {'path':path,'direction':direction,'streams':streams}
                evidence.append(row)
                try:
                    result = subprocess.run(['ip','netns','exec',receiver,'taskset','-c',str(cpu),
                        'iperf3','-c',sender,'-R','-J','-P',str(streams),'-t','8','-O','3',
                        '--connect-timeout','3000'], capture_output=True,text=True,timeout=35)
                    raw = json.loads(result.stdout)
                    row['raw'] = raw
                    if result.returncode:
                        raise ValueError(f'iperf3 exited {result.returncode}')
                    row['mbps'] = validate(raw, seconds=8)
                except ValueError as exc:
                    row['measurement_error'] = str(exc)
                except subprocess.TimeoutExpired:
                    row['measurement_error'] = 'capacity measurement exceeded 35 seconds'


def workloads(clients, dc, cores, front):
    single = []
    for client, domain, worker in zip(clients, DOMAINS, dc, strict=True):
        for direction in ('D','U'):
            initial = worker.call({'command':'stats'})['completed']
            client.send({'command':'single','domain':domain,'size':FILE_BYTES,'direction':direction})
            flow = client.receive(40)
            for _ in range(10):
                state = worker.call({'command':'stats'})
                if state['completed'] != initial:
                    break
                time.sleep(.05)
            if (flow['bytes']!=FILE_BYTES or not flow['sha256_verified']
                    or state['completed']!=initial+1 or state['errors']):
                raise RuntimeError('single-flow route or payload mismatch')
            single.append({'line':domain,'direction':direction,
                           'mbps':FILE_BYTES*8/1e6/flow['elapsed_seconds'],'flow':flow})

    pids = {'entry':front.pid, **{f'client{i}':c.process.pid for i,c in enumerate(clients,1)},
            **{f'dc{i}':c.process.pid for i,c in enumerate(dc,1)},
            **{f'core{i}':c.ready['pid'] for i,c in enumerate(cores,1)}}
    samples = []
    before = resources()
    started = time.monotonic()
    previous = {role:stats(pid) for role,pid in pids.items()}
    previous_time = started

    def observe(futures):
        nonlocal previous, previous_time
        while not all(f.done() for f in futures):
            now = time.monotonic()
            if now-started > 112:
                raise RuntimeError('mixed observation deadline')
            current = {role:stats(pid) for role,pid in pids.items()}
            elapsed = now-previous_time
            if elapsed >= .5:
                samples.append({
                    'offset_seconds':now-started,
                    'cpu_percent_one_core':{role:100*(current[role]['cpu_seconds']-old['cpu_seconds'])/elapsed
                                            for role,old in previous.items()},
                    'core_rss_bytes':[current[f'core{i}']['rss_bytes'] for i in (1,2)],
                    'core_sockets':[core.call({'command':'observe'}) for core in cores],
                    'router_qdiscs':qdiscs_in_namespace(NAMES[1]),
                })
                previous, previous_time = current, now
            time.sleep(.5)

    mixed = run_cohort(clients,dc,failed_over=False,payload_bytes=FILE_BYTES,observe=observe)
    after = resources()
    return {'single':single,'mixed':mixed,'samples':samples,'memory_peak_bytes':after['memory_peak'],
            'cpu_throttled_seconds':(after['cpu']['throttled_usec']-before['cpu']['throttled_usec'])/1e6,
            'memory_events_delta':{k:v-before['memory_events'][k] for k,v in after['memory_events'].items()}}


def verify_pair(original, candidate):
    left, right = original['mixed'], candidate['mixed']
    ratios = {d:right['rates_mbps'][d]/left['rates_mbps'][d] for d in ('D','U')}
    latency = []
    for old_line, new_line in zip(left['loaded'],right['loaded'],strict=True):
        latency.append({kind:{
            'loaded_ratio':new['loaded']['p95']/old_line[kind]['loaded']['p95'],
            'added_ms':new['loaded']['p95']-new['idle']['p95'],
            'samples':min(new['loaded']['n'],old_line[kind]['loaded']['n']),
        } for kind,new in new_line.items()})
    single_ratios = [new['mbps']/old['mbps'] for old,new in
                     zip(original['single'],candidate['single'],strict=True)]
    return {'mixed_ratios':ratios,'single_ratios':single_ratios,'latency':latency,
            'relative_and_latency_passed':min(*ratios.values(),*single_ratios)>=.95
            and min(left['bulk_overlap_seconds'],right['bulk_overlap_seconds'])>=1
            and all(v['loaded_ratio']<=1.10 and v['added_ms']<=100 and v['samples']>=20
                    for line in latency for v in line.values())}


def summarize(report):
    expected = [(turn,arm) for turn,order in enumerate((ARMS,ARMS[::-1]),1) for arm in order]
    if [(r['round'],r['arm']) for r in report['rows']] != expected:
        raise ValueError('gigabit trial order or coverage differs')
    calibration = {}
    for path in ('tcp','wireguard'):
        calibration[path] = {d:next((r.get('mbps') for r in report['capacity']
                                    if (r['path'],r['direction'],r['streams'])==(path,d,4)),None)
                             for d in ('D','U')}
    comparisons = {}
    for arm in ARMS[1:]:
        pairs = []
        for turn in (1,2):
            rows = {r['arm']:r for r in report['rows'] if r['round']==turn}
            pairs.append(verify_pair(rows['balanced2'],rows[arm]))
        comparisons[arm] = pairs
    return {
        'controls_900_mbps_both_directions':all(v is not None and v>=900
                                                for path in calibration.values() for v in path.values()),
        'four_stream_control_mbps':calibration,
        'comparisons_with_balanced2':comparisons,
        'mixed_900_mbps_arms':[arm for arm in ARMS if all(
            min(r['mixed']['rates_mbps'].values())>=900 for r in report['rows'] if r['arm']==arm)],
    }


def main(args):
    if os.geteuid()!=0 or os.environ.get('GITHUB_ACTIONS')!='true':
        raise RuntimeError('disposable CI only')
    before = resources()
    if (before['cpu_max'],before['memory_max'],before['swap_max']) != ('400000 100000',str(2048*1024**2),'0'):
        raise RuntimeError('unexpected gigabit experiment resource limits')
    cpus = sorted(os.sched_getaffinity(0))
    if len(cpus)<4:
        raise RuntimeError('four logical CPUs required')
    source = os.environ['TDH_SOURCE_COMMIT']
    candidate, pin = candidate_binary(args.candidate,'new',source)
    if pin['sha256']!=TLS_CANDIDATE_SHA:
        raise RuntimeError('unexpected candidate binary')
    report = {'source_commit':source,'binary_sha256':{'candidate':pin['sha256'],'balanced2':TLS_BASELINE[1]},
              'rtt_ms':100,'rate_mbps':1000,'payload_bytes_per_file':FILE_BYTES,
              'resources_before':before,'capacity':[],'rows':[],'complete':False}
    net = None
    children = []
    write(args.output,report)
    with tempfile.TemporaryDirectory(prefix='tdh-audit-gigabit-') as folder:
        root = Path(folder)
        try:
            baseline = root/'telemt-balanced2'
            payload,_ = archive_payload(args.balanced2_archive,TLS_BASELINE[0],
                                         TLS_BASELINE[2].removeprefix('telemt '),TLS_BASELINE[1],False)
            baseline.write_bytes(payload)
            baseline.chmod(0o755)
            del payload
            net = Network(SimpleNamespace(root=folder,wg='wg',iperf='iperf3',start_iperf=False,
                                          rtt_ms=100,rate_mbps=1000,loss_percent=0,mtu=1420,
                                          queue_limit_packets=50000))
            net.setup()
            second_tunnel(net)
            command('ip','netns','exec',NAMES[0],'tc','qdisc','replace','dev','tdh-la0','root',
                    'tbf','rate','1000mbit','burst','256kb','limit','31250000')
            report['tcp_limits'] = {ns:command('ip','netns','exec',ns,'sysctl','-n',
                                              'net.ipv4.tcp_rmem','net.ipv4.tcp_wmem')
                                    for ns in (NAMES[0],*BACKENDS)}
            report['iperf_version'] = command('iperf3','--version').splitlines()[0]
            report['cpu_assignment'] = dict(entry=cpus[0],backends=cpus[1:3],dc=cpus[3])
            capacity(net,report['capacity'],cpus[0],cpus[1])
            write(args.output,report)
            for ns,address in zip(BACKENDS,('198.18.0.1','198.18.0.5'),strict=True):
                command('ip','-n',ns,'address','add',address+'/32','dev','lo')
            dc = [Child('dc',ns,cpus[3],{'mixed':True},children) for ns in BACKENDS]
            clients = [Child('client',NAMES[0],cpus[0],{},children) for _ in DOMAINS]
            front = start_frontend(net)
            os.sched_setaffinity(front.pid,{cpus[0]})
            for turn, order in enumerate((ARMS,ARMS[::-1]),1):
                for arm in order:
                    shaped = arm=='candidate-shaped'
                    if shaped:
                        shape_backends_and_entry((450,450,450,450),install=True)
                    binary = baseline if arm=='balanced2' else candidate
                    version = [TLS_BASELINE[1],TLS_BASELINE[2]] if arm=='balanced2' else [pin['sha256'],pin['version']]
                    cores = []
                    try:
                        for ns,cpu in zip(BACKENDS,cpus[1:3],strict=True):
                            cores.append(Child('core',ns,cpu,{'kind':'balanced','binary':str(binary),
                                'synthetic_tls_front':True,'experimental_binary':version},children))
                        row = {'arm':arm,'round':turn,'queues_before':snapshot(),
                               **workloads(clients,dc,cores,front),'queues_after':snapshot()}
                        report['rows'].append(row)
                        write(args.output,report)
                    finally:
                        for core in cores:
                            core.close()
                        if shaped:
                            for ns,dev in LINKS:
                                command('ip','netns','exec',ns,'tc','qdisc','del','dev',dev,'root')
            report['summary'] = summarize(report)
            report['complete'] = True
        except (Exception,KeyboardInterrupt) as exc:
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
            delta = {k:after['memory_events'][k]-before['memory_events'][k] for k in ('oom','oom_kill')}
            report.update(resources_after=after,cleanup_errors=errors,oom_delta=delta,
                          accepted=report['complete'] and not errors and not any(delta.values())
                          and after['pids_current']==1)
            write(args.output,report)
    return 0 if report['accepted'] else 1


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--balanced2-archive', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    raise SystemExit(main(parser.parse_args()))
