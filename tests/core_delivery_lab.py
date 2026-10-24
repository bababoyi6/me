"""One bounded installed-audit2/balanced2 correctness cohort, never a speed benchmark."""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

from e2e_lab import FullLab,close_on_error
from network_lab import Network,NAMES

HERE=Path(__file__).resolve()


def save(path,value):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,indent=2)+'\n');temporary.replace(path)


def child(root):
    if subprocess.check_output(['ip','netns','identify'],text=True,timeout=3).strip()!=NAMES[0]:
        raise RuntimeError('delivery worker outside private client namespace')
    rows,probes=[],[]
    result={'classification':'CI_INSTALLED_CORE_LARGE_TRANSFER_CORRECTNESS',
            'scope':'actual installed cores; HAProxy/Telemt to 100ms synthetic DC; payload does not traverse WG',
            'performance_comparison_claimed':False,'rows':rows,'small_probes':probes,'accepted':False}
    output=root/'results/delivery-transfers.json'
    try:
        for channel in ('audit2','balanced2'):
            metadata=json.loads((root/(channel+'-build.json')).read_text())
            lab=FullLab(root/('telemt-'+channel),dc_endpoint=('198.51.100.2',26000),
                        experimental_binary=(metadata['sha256'],metadata['version']),
                        artifact_registry=root/'artifact-registry')
            try:
                for domain,direction in (('apple.com','D'),('gs.apple.com','U'),('gs.apple.com','D'),('apple.com','U')):
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool,close_on_error(lab):
                        future=pool.submit(lab.transfer,domain,direction,8*1024**2,1250000,
                                           io_timeout=25,persistent=True,fragment=True)
                        for _ in range(3):
                            time.sleep(.5)
                            if future.done():
                                break
                            probe=lab.transfer('gs.apple.com','D',1024,1250000,io_timeout=5)
                            probes.append(dict(probe,channel=channel,during_direction=direction))
                        row=future.result(timeout=25)
                    rows.append(dict(row,channel=channel,binary_sha256=metadata['sha256'],slow_reader=False))
                    save(output,result)
                row=lab.transfer('apple.com','D',2*1024**2,1250000,io_timeout=25,persistent=True,
                                 read_pause=1,receive_buffer=32768)
                rows.append(dict(row,channel=channel,binary_sha256=metadata['sha256'],slow_reader=True))
                lab.validate(0)
            finally:
                lab.close()
            save(output,result)
        if (len(rows)!=10 or not probes or not all(row['sha256_verified'] for row in rows+probes) or
                sum(row['bytes'] for row in rows+probes)>72*1024**2):
            raise RuntimeError('delivery correctness or payload contract failed')
        result.update(accepted=True,payload_bytes=sum(row['bytes'] for row in rows+probes))
    except Exception as exc:
        result['error']={'type':type(exc).__name__,'phase':getattr(exc,'lab_phase',None)}
        raise
    finally:
        save(output,result)


def run(root):
    net=Network(SimpleNamespace(root=str(root),wg='wg',start_iperf=False,
                               rtt_ms=100,loss_percent=0,rate_mbps=100,mtu=1420))
    result={'classification':'CI_CORE_DELIVERY','accepted':False,'dc':None,'network_cleanup_verified':False}
    try:
        with tempfile.TemporaryDirectory(prefix='tdh-audit-delivery-') as folder:
            shared=Path(folder);os.chown(shared,65534,65534)
            net.setup()
            process=subprocess.Popen(['ip','netns','exec',NAMES[2],sys.executable,
                str(HERE.with_name('mock_dc_worker.py')),'--output',str(shared/'dc.json'),'--socket-timeout','25'],
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            net.processes.append(process)
            deadline=time.monotonic()+5
            while not (shared/'dc.json').exists():
                if process.poll() is not None or time.monotonic()>deadline:
                    raise RuntimeError('private delivery DC readiness failed')
                time.sleep(.1)
            worker=subprocess.run(['ip','netns','exec',NAMES[0],sys.executable,str(HERE),
                '--root',str(root),'--child'],capture_output=True,text=True,timeout=140)
            (root/'results/delivery-worker.log').write_text(worker.stderr[-4000:])
            time.sleep(.2)
            result['dc']=json.loads((shared/'dc.json').read_text())
            if worker.returncode:
                raise RuntimeError('delivery transfer child failed; partial report retained')
            transfers=json.loads((root/'results/delivery-transfers.json').read_text())
            if (not transfers['accepted'] or result['dc']['errors'] or
                    result['dc']['completed']!=len(transfers['rows'])+len(transfers['small_probes'])):
                raise RuntimeError('delivery DC integrity/completion mismatch')
            result['accepted']=True
    except Exception as exc:
        result['error_type']=type(exc).__name__
        raise
    finally:
        try:
            net.close();result['network_cleanup_verified']=True
        finally:
            save(root/'results/delivery-verdict.json',result)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True)
    parser.add_argument('--child',action='store_true')
    args=parser.parse_args();root=Path(args.root)
    if (os.geteuid()!=0 or not root.is_absolute() or root.resolve()!=root or root.is_symlink() or
            root.name!='tdh-audit-core-delivery' or root.stat().st_uid!=0 or root.stat().st_mode & 0o077):
        parser.error('private root-owned delivery directory required')
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    child(root) if args.child else run(root)


if __name__=='__main__':
    main()
