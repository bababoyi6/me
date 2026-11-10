"""Three-minute bounded connection churn and resource accounting, not an endurance claim."""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import platform
import tempfile
import time

from e2e_lab import FullLab
from stable_acceptance import PINS,stats
from release_pins import RELEASES
from lab_files import file_sha256 as digest
import tarfile


def cgroup_stats():
    group=next(line.split(':',2)[2] for line in Path('/proc/self/cgroup').read_text().splitlines()
               if line.startswith('0::'))
    if not Path(group).name.startswith('tdh-churn-'):
        raise RuntimeError('dedicated resource-bounded transient unit required')
    root=Path('/sys/fs/cgroup')/group.lstrip('/')
    events={k:int(v) for k,v in (line.split() for line in (root/'memory.events').read_text().splitlines())}
    cpu={k:int(v) for k,v in (line.split() for line in (root/'cpu.stat').read_text().splitlines())}
    return {'memory_current':int((root/'memory.current').read_text()),
            'memory_peak':int((root/'memory.peak').read_text()),'memory_events':events,'cpu':cpu,
            'pids_current':int((root/'pids.current').read_text()),
            'memory_max':(root/'memory.max').read_text().strip(),
            'swap_max':(root/'memory.swap.max').read_text().strip()}


def assess(report):
    samples=report.get('samples',[])
    if report.get('elapsed_seconds',0)<180 or len(samples)<20 or report.get('errors'):
        raise ValueError('incomplete bounded resource observation')
    if (report.get('config_unchanged') is not True or report.get('all_payloads_verified') is not True or
            report.get('private_processes_closed') is not True or report.get('mock_dc_errors')):
        raise ValueError('protocol or cleanup failure')
    before,after=report['cgroup_before'],report['cgroup_after']
    if any(after['memory_events'][key]!=before['memory_events'][key] for key in ('oom','oom_kill')):
        raise ValueError('cgroup OOM event')
    if report['fd_after_idle']>report['fd_before']+8:
        raise ValueError('descriptors did not return within bounded headroom')
    if report['rss_growth_after_warmup']>16*1024**2 or max(s['rss_bytes'] for s in samples)>128*1024**2:
        raise ValueError('backend resident memory exceeded the observation bounds')
    return True


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--stable-release',choices=tuple(RELEASES),default='3.5.16')
    args=parser.parse_args()
    pin=RELEASES[args.stable_release]
    if os.geteuid()!=0 or digest(args.archive)!=pin[0]:
        raise RuntimeError('root and exact official archive required')
    report={'scope':'180-second synthetic native protocol connection churn; no 24h or real-client claim',
            'os':platform.freedesktop_os_release().get('PRETTY_NAME'),'accepted':False,
            'samples':[],'errors':[],'connections_per_wave':30,'payload_per_connection':4096}
    lab=None
    with tempfile.TemporaryDirectory(prefix='tdh-churn-binary-') as temp:
        root=Path(temp)
        binary=root/'telemt'
        with tarfile.open(args.archive,'r:gz') as archive:
            member=next(m for m in archive if m.name=='telemt' and m.isfile() and m.size<64*1024**2)
            binary.write_bytes(archive.extractfile(member).read())
        binary.chmod(0o755)
        if digest(binary)!=pin[1]:raise RuntimeError('binary digest mismatch')
        try:
            report['cgroup_before']=cgroup_stats()
            lab=FullLab(binary,offline_lab=True)
            report['fd_before']=stats(lab.telemt.pid)['fds']
            started=time.monotonic()
            completed=0
            while time.monotonic()-started<180:
                with concurrent.futures.ThreadPoolExecutor(max_workers=30) as pool:
                    futures=[pool.submit(lab.transfer,('apple.com','gs.apple.com')[i%2],('U','D')[i%2],
                                         4096,1000000,persistent=True) for i in range(30)]
                    for future in futures:
                        try:
                            future.result();completed+=1
                        except Exception as error:
                            report['errors'].append({'type':type(error).__name__,'phase':getattr(error,'lab_phase','unknown')})
                # Take the minimum FD sample over a one-second quiet window to
                # avoid mistaking a periodic background DC probe for a leaked fd.
                quiet=[]
                for _ in range(5):
                    quiet.append(stats(lab.telemt.pid));time.sleep(.2)
                sample=quiet[-1]
                sample.update(fds=min(row['fds'] for row in quiet),elapsed_seconds=time.monotonic()-started)
                report['samples'].append(sample)
                report.update(elapsed_seconds=time.monotonic()-started,completed=completed)
                Path(args.output).write_text(json.dumps(report,indent=2)+'\n')
                if report['errors']:raise RuntimeError('native protocol churn failed')
                time.sleep(max(0,min(5,180-(time.monotonic()-started))))
            time.sleep(2)
            report['fd_after_idle']=min(stats(lab.telemt.pid)['fds'] for _ in range(3))
            report['rss_growth_after_warmup']=report['samples'][-1]['rss_bytes']-report['samples'][4]['rss_bytes']
            report['config_unchanged']=digest(lab.telemt_config)==lab.config_hash
            lab.validate(completed)
            report.update(all_payloads_verified=True,mock_dc_errors=lab.dc.errors,
                          elapsed_seconds=time.monotonic()-started)
        except Exception as error:
            report['failure_type']=type(error).__name__
            raise
        finally:
            if lab is not None:lab.close()
            report['private_processes_closed']=lab is None or all(p.poll() is not None for p in lab.processes)
            report['cgroup_after']=cgroup_stats()
            try:report['accepted']=assess(report)
            except (ValueError,KeyError):pass
            Path(args.output).write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'accepted':report['accepted'],'completed':report['completed']}))
    return 0 if report['accepted'] else 1


if __name__=='__main__':
    raise SystemExit(main())
