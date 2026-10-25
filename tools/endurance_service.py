"""One bounded persistent-path service; its cleanup fuse outlives the worker.

The controller, mock DC, HAProxy, tunnel workers and both cores all share ONE
resource-limited systemd unit, including the controller's memory and CPU.
"""
import argparse
from datetime import datetime,timezone
import gc
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import signal
import shutil
import subprocess
import sys
import time
import uuid

from lab_preflight import inventory,read,MIB

REPO=Path(__file__).resolve().parents[1]


def save(path,value):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,indent=2)+'\n');temporary.replace(path)


def launch(args):
    root=Path(args.root)
    assert os.geteuid()==0 and root.is_absolute() and root.resolve()==root and not root.exists()
    assert re.fullmatch(r'tdh-audit-endurance-[0-9a-f]{12}',root.name) or (
        os.environ.get('GITHUB_ACTIONS')=='true' and root.name=='tdh-audit-endurance-pilot')
    report=inventory()
    limits=report['budget']
    assert limits['eligible'] and not report['existing_lab_namespaces']
    assert report['kernel_oom_kill_count'] is not None
    assert {'cpu','memory','pids'}.issubset(report['cgroup_v2_controllers'])
    bundle=Path(args.bundle).resolve()
    assert bundle.is_file() and not bundle.is_symlink() and bundle.stat().st_size==10171361
    assert hashlib.sha256(bundle.read_bytes()).hexdigest()=='b4fec3efd10288a9f211ad958337fa314a15486e72fd5c50ab870f22a19737e5'
    os.umask(0o077);root.mkdir(mode=0o700);(root/'results').mkdir(mode=0o700)
    unit='tdh-audit-endurance-'+uuid.uuid4().hex[:12]+'.service'
    plan={'unit':unit,'seconds':args.seconds,'bundle':str(bundle),'preflight':report,
          'started_utc':datetime.now(timezone.utc).isoformat(),'all_controller_and_worker_resources_share_one_unit':True,
          'source_sha256':{str(p.relative_to(REPO)):hashlib.sha256(p.read_bytes()).hexdigest()
                           for folder in ('tools','tests') for p in sorted((REPO/folder).glob('*.py'))}}
    save(root/'plan.json',plan)
    cleanup=shlex.join([sys.executable,str(REPO/'tools/endurance_cleanup.py'),'--root',str(root)])
    argv=['systemd-run','--quiet','--service-type=exec','--unit='+unit,
          '--property=CPUQuota=60%',f'--property=MemoryMax={limits["memory_max_bytes"]}',
          f'--property=MemoryHigh={limits["memory_high_bytes"]}','--property=MemorySwapMax=0',
          '--property=TasksMax=192','--property=OOMPolicy=stop','--property=KillMode=control-group',
          '--property=TimeoutStopSec=20',f'--property=RuntimeMaxSec={args.seconds+180}',
          '--property=UMask=0077',f'--property=LimitFSIZE={32*MIB}',
          '--property=StandardOutput=append:'+str(root/'worker.log'),'--property=StandardError=inherit',
          '--property=ExecStopPost='+cleanup,
          *(['--setenv=GITHUB_ACTIONS=true'] if os.environ.get('GITHUB_ACTIONS')=='true' else []),
          *(['--wait'] if args.wait else ['--no-block']),
          sys.executable,str(Path(__file__).resolve()),'--worker','--root',str(root)]
    subprocess.run(argv,check=True,timeout=args.seconds+230 if args.wait else 20)
    if args.wait:
        status=json.loads((root/'results/service.json').read_text())
        fuse=json.loads((root/'fuse-cleanup.json').read_text())
        assert status['accepted'] and fuse['complete']
    print(json.dumps({'unit':unit,'root':str(root),'seconds':args.seconds,'completed':bool(args.wait)}))


def worker(root):
    os.umask(0o077)
    plan=json.loads((root/'plan.json').read_text())
    expected='/system.slice/'+plan['unit']
    group=next(x.split(':',2)[2] for x in Path('/proc/self/cgroup').read_text().splitlines() if x.startswith('0::'))
    assert group==expected
    cg=Path('/sys/fs/cgroup'+group)
    from candidate_bundle import CORE_SHA256,VERSION
    from core_install import archive_payload,durable_file
    binary,documents=archive_payload(plan['bundle'],'b4fec3efd10288a9f211ad958337fa314a15486e72fd5c50ab870f22a19737e5',VERSION,CORE_SHA256,True)
    durable_file(root/'telemt-bulk',binary,0o755)
    notice=root/'core-source-notice';notice.mkdir(mode=0o700)
    for name,content in documents.items():durable_file(notice/name,content,0o644)
    save(root/'bulk-build.json',{'sha256':CORE_SHA256,'version':'telemt '+VERSION})
    del binary,documents;gc.collect()
    result={'accepted':False,'exit':None,'stop_reason':None,'samples':[],
            'host_oom_delta':None,'private_keys_exported':False}
    started=time.monotonic();process=None;last=-30
    limits=plan['preflight']['budget']
    try:
        with (root/'path.log').open('w') as log:
            process=subprocess.Popen([sys.executable,'-B',str(REPO/'tests/dualhop_lab.py'),
                '--root',str(root),'--build','bulk','--separate-dc','--rtt','100',
                '--endurance-seconds',str(plan['seconds']),'run'],stdout=log,stderr=log)
            while process.poll() is None:
                elapsed=time.monotonic()-started
                available=next(int(line.split()[1])*1024 for line in read('/proc/meminfo').splitlines() if line.startswith('MemAvailable:'))
                oom=int(next(line.split()[1] for line in read('/proc/vmstat').splitlines() if line.startswith('oom_kill ')))
                events=dict((k,int(v)) for k,v in (line.split() for line in (cg/'memory.events').read_text().splitlines()))
                assert available>=limits['host_reserve_bytes'],'host memory reserve'
                assert oom==plan['preflight']['kernel_oom_kill_count'],'host OOM'
                assert not events.get('oom',0),'cgroup OOM'
                assert elapsed<plan['seconds']+150,'controller deadline'
                assert sum(p.stat().st_size for p in root.rglob('*.log'))<8*MIB,'log cap'
                assert shutil.disk_usage(root).free>=512*MIB,'host disk reserve'
                if elapsed-last>=30:
                    result['samples'].append({'elapsed_sec':round(elapsed,1),'memory_current':int((cg/'memory.current').read_text()),
                         'host_available':available,'memory_events':events,'cpu_stat':(cg/'cpu.stat').read_text()})
                    save(root/'results/service.json',result);last=elapsed
                time.sleep(1)
            result['exit']=process.returncode
            sys.path.insert(0,str(REPO/'tests'))
            from endurance_contract import validate
            assert process.returncode==0,'path process failed'
            validate(json.loads((root/'results/endurance.json').read_text()))
            result['accepted']=True
    except BaseException as error:
        result['stop_reason']=str(error) if isinstance(error,AssertionError) else type(error).__name__
        raise
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:process.wait(timeout=12)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)
        result['elapsed_sec']=time.monotonic()-started
        result['host_oom_delta']=int(next(line.split()[1] for line in read('/proc/vmstat').splitlines() if line.startswith('oom_kill ')))-plan['preflight']['kernel_oom_kill_count']
        save(root/'results/service.json',result)


if __name__=='__main__':
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True)
    parser.add_argument('--bundle')
    parser.add_argument('--seconds',type=int,choices=(60,86400),default=60)
    parser.add_argument('--wait',action='store_true')
    parser.add_argument('--worker',action='store_true')
    args=parser.parse_args()
    if args.worker:worker(Path(args.root))
    else:launch(args)
