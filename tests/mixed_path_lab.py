"""Three pinned cores, mixed experience, split CPUs and real private WireGuard."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import select
import signal
import subprocess
import tempfile
import time
from types import SimpleNamespace

from long_path_lab import Child, NAMES, command
from network_lab import Network
from kernel_observer import qdiscs_in_namespace
from paired_performance import resources, write
from core_install import archive_payload
from stable_acceptance import PINS, stats
from release_pins import RELEASES
from legacy_queue_analysis import NATIVE_SHA, UPSTREAM, BUILD_SOURCE
from path_capacity import controls as capacity_controls
from queue_probe import original_wg_policy, configure_private_shaper, private_wg_snapshot
from tls_candidate_lab import candidate_binary

PROFILES={'low':(0,200,0),'rtt100':(100,200,0),'rtt200':(200,200,0),'weak':(100,100,.1)}
VARIANTS=('balanced2','official17','native17')


def main(args):
    if os.geteuid()!=0 or os.environ.get('GITHUB_ACTIONS')!='true':raise RuntimeError('disposable CI only')
    before=resources()
    if (before['cpu_max']!='300000 100000' or before['memory_max']!=str(1536*1024**2) or before['swap_max']!='0'):
        raise RuntimeError('registered 3 CPU / 1.5GiB / zero swap budget required')
    cpus=sorted(os.sched_getaffinity(0))
    if len(cpus)<3:raise RuntimeError('separate core/client/DC logical CPUs required')
    backend_cpu,frontend_cpu,dc_cpu=cpus[:3];os.sched_setaffinity(0,{frontend_cpu})
    rtt,rate,loss=PROFILES[args.profile]
    queue_screen=getattr(args,'queue_screen',False)
    queue_core=getattr(args,'queue_core','balanced2')
    managed=getattr(args,'managed_backend_queue',False)
    candidate_queue=args.new_tls_queue
    candidate_screen=args.new_tls_candidate or candidate_queue
    if candidate_queue and (args.new_tls_candidate or queue_screen or managed):
        raise RuntimeError('choose one candidate study')
    if candidate_screen and (queue_screen or managed or args.client_diagnostic or args.profile!='rtt100'):
        raise RuntimeError('new TLS mixed screen requires the 100 ms profile')
    if candidate_queue:
        queue_screen=True
        queue_core='candidate'
    if not candidate_screen and not args.stable_archive:
        raise RuntimeError('official archive required for the historical modes')
    if managed and not queue_screen:raise RuntimeError('managed backend queue requires registered screening mode')
    if queue_screen and (args.client_diagnostic or args.profile!='rtt100'):
        raise RuntimeError('shaper screen is registered only for the 100ms mixed workload')
    output=Path(args.output)
    report={'plan':'client-small-write-fullpath-v1' if args.client_diagnostic else 'mixed-experience-v1','source_commit':os.environ['TDH_SOURCE_COMMIT'],
        'profile':args.profile,'rtt_ms':rtt,'rate_mbps':rate,'loss_percent':loss,
        'scope':'2 persistent 64-byte echo streams, 4 new FakeTLS+1KiB/ACK workers, 4D+1U files, idle with 5s application keepalive; not Telegram users',
        'complete':False,'accepted':False,'rows':[],'controls':[],'resources_before':before,
        'cpu_assignment':{'core':backend_cpu,'client_haproxy_observer':frontend_cpu,'dc':dc_cpu},
        'cpu_model':next(s.split(':',1)[1].strip() for s in Path('/proc/cpuinfo').read_text().splitlines() if s.startswith('model name'))}
    if args.client_diagnostic:
        report['scope']='one client TCP_NODELAY variable; sequential new FakeTLS+1KiB/ACK, four AB/BA rounds per pinned core; no bulk/idle workload or throughput claim'
    if candidate_screen:
        report['plan']='tls-mixed-x86-v1'
        report['scope']+='; 30 logical connections, 32 MiB per file, AB/BA'
    if queue_screen:
        report['plan']='managed-backend-screen-v1' if managed else 'private-shaper-screen-v1'
        report['queue_core']=queue_core
        report['queue_telemetry']='plain-tc-v2'
        report['scope']='one pinned core only; four AB/BA mixed pairs, 30 logical connections; pre-encryption 180Mbps HTB/fq_codel versus unchanged 200Mbps WAN'
        if managed:report['scope']='exact opt-in controller on backend WG only; entry unchanged; four AB/BA mixed pairs, 30 logical connections'
    if candidate_queue:
        report['plan']='tls-candidate-shaper-v1'
        report['scope']='two AB/BA pairs, 30 logical connections, 32 MiB per file; dual-end 180 Mbps HTB/fq_codel before encryption'
    children=[];net=None;front=None;dc=None;expected=0
    write(output,report)
    try:
        with tempfile.TemporaryDirectory(prefix='tdh-audit-mixed-') as folder:
            root=Path(folder)
            bundle=Path(args.native_directory);manifest=json.loads((bundle/'manifest.json').read_text())
            binaries={};pins={'balanced2':PINS['balanced2'],'official17':RELEASES['3.5.17']}
            archives=[('balanced2',args.balanced2_archive)]
            if not candidate_screen:
                archives.append(('official17',args.stable_archive))
            for variant,archive in archives:
                pin=pins[variant];payload,_=archive_payload(archive,pin[0],pin[2].removeprefix('telemt '),pin[1],False)
                target=root/variant;target.write_bytes(payload);target.chmod(0o755);binaries[variant]=str(target)
            if candidate_screen:
                binary, _ = candidate_binary(bundle,'new',report['source_commit'])
                binaries['candidate']=str(binary)
                report['candidate_manifest']=manifest
            else:
                if (manifest['upstream_commit']!=UPSTREAM or manifest['manager_commit']!=BUILD_SOURCE or
                        manifest['binaries']['balanced']['sha256']!=NATIVE_SHA):raise RuntimeError('native identity drift')
                native=(bundle/'telemt-balanced').read_bytes()
                if hashlib.sha256(native).hexdigest()!=NATIVE_SHA:raise RuntimeError('native binary digest mismatch')
                target=root/'native17';target.write_bytes(native);target.chmod(0o755);binaries['native17']=str(target)
            report['binary_sha256']={key:hashlib.sha256(Path(path).read_bytes()).hexdigest() for key,path in binaries.items()}
            net=Network(SimpleNamespace(root=str(root),wg='wg',iperf='iperf3',start_iperf=True,
                rtt_ms=rtt,rate_mbps=rate,loss_percent=loss,mtu=1420))
            net.setup()
            command('ip','-n',NAMES[2],'address','add','198.18.0.1/32','dev','lo')
            report['capacity_recipe']='receiver-local-v2; full JSON, matched windows and bytes required'
            capacity_controls(net,rate,report['controls'])
            if queue_screen and any(c['mbps']<rate*.5 for c in report['controls']):
                raise RuntimeError('capacity control below unchanged 100Mbps gate; no screening load permitted')
            wg_original=original_wg_policy() if queue_screen and not managed else None
            def configure_queue(enabled):
                if managed:
                    command('ip','netns','exec',NAMES[2],'python3',str(Path(__file__).with_name('managed_queue_worker.py')),
                        str(root),'enable' if enabled else 'disable',timeout=30)
                else:configure_private_shaper(enabled,wg_original)
            def snapshot_queue():
                if managed:
                    return {NAMES[2]:{kind:command('ip','netns','exec',NAMES[2],
                        'tc','-s','-d',kind,'show','dev','tdh-b1') for kind in ('qdisc','class')}}
                return private_wg_snapshot()
            if managed:
                command('ip','-n',NAMES[2],'link','set','dev','wg-lab','name','tdh-b1')
                manager=root/'managed-manager';manager.write_bytes((Path(__file__).resolve().parents[1]/'telemt-dual-hop.sh').read_bytes());manager.chmod(0o755)
            if queue_screen:
                report['initial_wireguard']=snapshot_queue()
                configure_queue(True)
                report['shaped_preflight']=snapshot_queue()
                configure_queue(False)
            dc=Child('dc',NAMES[2],dc_cpu,{'mixed':True},children)
            client=Child('client',NAMES[0],frontend_cpu,{},children)
            config=root/'haproxy.cfg'
            config.write_text('''global
    nbthread 1
defaults
    mode tcp
    timeout connect 5s
    timeout client 30s
    timeout server 30s
frontend lab
    bind 127.0.0.1:24443
    tcp-request inspect-delay 2s
    tcp-request content accept if { req.ssl_hello_type 1 }
    use_backend line_b if { req.ssl_sni -i gs.apple.com }
    default_backend line_a
backend line_a
    server private 10.77.1.2:24431 send-proxy-v2
backend line_b
    server private 10.77.1.2:24431 send-proxy-v2
''')
            front=subprocess.Popen(['ip','netns','exec',NAMES[0],'haproxy','-db','-f',str(config)],
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
            time.sleep(.3)
            if front.poll() is not None:raise RuntimeError('private SNI frontend startup failed')
            variants=('balanced2','candidate') if candidate_screen else VARIANTS
            rounds=2 if candidate_screen else 4 if queue_screen else 1 if args.client_diagnostic else 3
            for turn in range(rounds):
                order=(queue_core,) if queue_screen else variants[turn:]+variants[:turn]
                for variant in order:
                    options={'kind':'balanced2' if variant=='balanced2' else 'balanced','binary':binaries[variant]}
                    if candidate_screen:
                        options['synthetic_tls_front']=True
                    if variant!='balanced2':options['experimental_binary']=[report['binary_sha256'][variant],
                        RELEASES['3.5.17'][2] if variant=='official17' else manifest['binaries']['balanced']['version']]
                    backend=Child('core',NAMES[2],backend_cpu,options,children)
                    try:
                        warm=client.call({'command':'warm'})
                        if warm['errors']:raise RuntimeError('warmup failed')
                        expected+=4
                        if args.client_diagnostic:
                            for repeat in range(4):
                                for enabled in ((False,True) if repeat%2==0 else (True,False)):
                                    client.send({'command':'probe-client-policy','nodelay':enabled})
                                    result=client.receive(30)
                                    if len(result['rows'])!=10 or any(row['client_nodelay']!=int(enabled) for row in result['rows']):
                                        raise RuntimeError('client policy diagnostic incomplete')
                                    report['rows'].append({'round':repeat+1,'variant':variant,'client_nodelay':enabled,'transfers':result['rows'],
                                        'config_policy_sha256':backend.ready['config_policy_sha256']})
                                    expected+=10;write(output,report)
                            state=dc.call({'command':'stats'})
                            if state['completed']!=expected or state['errors']:raise RuntimeError('diagnostic DC mismatch')
                            continue
                        idle_before=stats(backend.ready['pid'])
                        cohorts=(30,30) if queue_screen else (30,) if candidate_screen else ((30,50,100) if turn%2==0 else (100,50,30))
                        for position,cohort in enumerate(cohorts):
                            shaped=bool(position if turn%2==0 else 1-position)
                            if queue_screen:
                                configure_queue(shaped)
                                time.sleep(1)
                                queue_before=snapshot_queue()
                            group_cpu_before=resources()['cpu']['usage_usec']
                            ids={'backend':backend.ready['pid'],'client':client.ready['pid'],'dc':dc.ready['pid'],'haproxy':front.pid}
                            snapshots={k:stats(pid) for k,pid in ids.items()}
                            start=time.monotonic();parent_cpu=time.process_time()
                            usage=resource.getrusage(resource.RUSAGE_CHILDREN);child_cpu=usage.ru_utime+usage.ru_stime
                            sockets=[];queues=[];rss=[];cpu_intervals=[];last=-1
                            previous=snapshots;previous_time=start
                            client.send({'command':'mixed','logical_clients':cohort,'candidate_screen':candidate_screen})
                            while not select.select([client.process.stdout],[],[],.1)[0]:
                                if time.monotonic()-start>115:raise RuntimeError('mixed trial deadline')
                                if time.monotonic()-start-last>=1:
                                    offset=time.monotonic()-start
                                    sockets.append({'offset_seconds':offset,'rows':backend.call({'command':'observe'})})
                                    queues.append({'offset_seconds':offset,'rows':qdiscs_in_namespace(NAMES[1])})
                                    now=time.monotonic();current={k:stats(pid) for k,pid in ids.items()}
                                    if now-previous_time>=.5:
                                        cpu_intervals.append({'offset_seconds':now-start,'seconds':now-previous_time,
                                            'percent':{k:100*(current[k]['cpu_seconds']-previous[k]['cpu_seconds'])/(now-previous_time) for k in ids}})
                                        previous=current;previous_time=now
                                    rss.append(current['backend']['rss_bytes']);last=time.monotonic()-start
                            result=client.receive();elapsed=time.monotonic()-start
                            cpu={key:100*(stats(pid)['cpu_seconds']-snapshots[key]['cpu_seconds'])/elapsed for key,pid in ids.items()}
                            cpu['controller']=100*(time.process_time()-parent_cpu)/elapsed
                            usage=resource.getrusage(resource.RUSAGE_CHILDREN)
                            cpu['observer_children']=100*(usage.ru_utime+usage.ru_stime-child_cpu)/elapsed
                            affinities=[sorted(os.sched_getaffinity(int(task.name))) for task in Path(f"/proc/{backend.ready['pid']}/task").iterdir()]
                            if any(a!=[backend_cpu] for a in affinities):raise RuntimeError('backend escaped single CPU')
                            report['rows'].append({'round':turn+1,'variant':variant,'cohort':cohort,'wall_seconds':elapsed,
                                'result':result,'cpu_percent_one_core':cpu,'socket_samples':sockets,'qdisc_samples':queues,
                                'process_cpu_intervals':cpu_intervals,
                                'sampled_backend_peak_rss':max(rss) if rss else None,'backend_thread_affinities':affinities,
                                'config_policy_sha256':backend.ready['config_policy_sha256']})
                            if queue_screen:
                                report['rows'][-1]['queue_policy']='shaped180' if shaped else 'original'
                                report['rows'][-1]['group_cpu_seconds']=(resources()['cpu']['usage_usec']-group_cpu_before)/1e6
                                report['rows'][-1]['wireguard_before']=queue_before
                                report['rows'][-1]['wireguard_after']=snapshot_queue()
                            write(output,report)
                            if not result['complete'] or result['errors']:raise RuntimeError('mixed integrity or completion failure')
                            expected+=result['dc_connections']
                            if queue_screen:
                                configure_queue(False)
                                time.sleep(1)
                        time.sleep(2)
                        idle_after=stats(backend.ready['pid'])
                        report['rows'][-1]['idle_before']=idle_before;report['rows'][-1]['idle_after']=idle_after
                        if idle_after['fds']>idle_before['fds']+2:raise RuntimeError('idle descriptors did not recover')
                        state=dc.call({'command':'stats'})
                        if state['completed']!=expected or state['errors']:raise RuntimeError('mock DC complete/integrity mismatch')
                    finally:backend.close()
            report['mock_dc']=dc.call({'command':'stats'});report['complete']=True
    finally:
        report['cleanup_errors']=[]
        if dc is not None and dc.process.poll() is None:
            try:report['mock_dc']=dc.call({'command':'stats'})
            except Exception as exc:report['dc_observation_error']=type(exc).__name__
        for child in reversed(children):
            try:child.close()
            except Exception as exc:report['cleanup_errors'].append(type(exc).__name__)
        if front is not None and front.poll() is None:
            front.terminate()
            try:front.wait(timeout=3)
            except subprocess.TimeoutExpired:os.killpg(front.pid,signal.SIGKILL);front.wait(timeout=3)
        if net is not None:
            try:net.close()
            except Exception as exc:report['cleanup_errors'].append(type(exc).__name__)
        report['private_processes_closed']=all(child.process.poll() is not None for child in children) and (front is None or front.poll() is not None)
        report['namespaces_removed']=not set(NAMES).intersection(line.split()[0] for line in command('ip','netns','list').splitlines())
        report['resources_after']=resources()
        report['oom_delta']={key:report['resources_after']['memory_events'][key]-before['memory_events'][key] for key in ('oom','oom_kill')}
        report['accepted']=(report['complete'] and report['private_processes_closed'] and report['namespaces_removed']
            and not report['cleanup_errors'] and not any(report['oom_delta'].values()))
        write(output,report)
    return 0 if report['accepted'] else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',choices=tuple(PROFILES),required=True)
    parser.add_argument('--balanced2-archive',required=True);parser.add_argument('--stable-archive')
    parser.add_argument('--native-directory',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--client-diagnostic',action='store_true')
    parser.add_argument('--queue-screen',action='store_true')
    parser.add_argument('--queue-core',choices=('balanced2','native17'),default='balanced2')
    parser.add_argument('--managed-backend-queue',action='store_true')
    parser.add_argument('--new-tls-candidate',action='store_true')
    parser.add_argument('--new-tls-queue',action='store_true')
    signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
    raise SystemExit(main(parser.parse_args()))
