"""Bounded synthetic protocol regression for explicitly pinned native lab builds."""
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import time

from e2e_lab import FullLab, sanitize_startup_log
from paired_performance import resources, write
from stable_acceptance import stats


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args()
    if (os.geteuid()!=0 or os.environ.get('GITHUB_ACTIONS')!='true' or
            os.stat('/proc/self/ns/net').st_ino==os.stat('/proc/1/ns/net').st_ino):
        raise RuntimeError('owned CI namespace required')
    before=resources();directory=Path(args.directory)
    manifest=json.loads((directory/'manifest.json').read_text())
    if manifest['upstream_commit']!='84e5b827a6619fb2e09ad3d09f253cdffe04bc2f':raise RuntimeError('source mismatch')
    report={'source_commit':os.environ['TDH_SOURCE_COMMIT'],'native_manifest':manifest,'accepted':False,'rows':[],
        'resources_before':before,'private_processes_closed':False,
        'mock_dc_socket_timeout_seconds':25,'client_io_timeout_seconds':25}
    write(Path(args.output),report)
    processes=[]
    complete=False
    try:
        for kind in ('control','balanced'):
            binary=directory/('telemt-'+kind);pin=manifest['binaries'][kind]
            if hashlib.sha256(binary.read_bytes()).hexdigest()!=pin['sha256']:raise RuntimeError('binary mismatch')
            lab=FullLab(binary,experimental_binary=(pin['sha256'],pin['version']),offline_lab=True)
            # Match both protocol endpoint deadlines under a queued 100-flow burst.
            lab.dc.socket_timeout=25
            processes.extend(lab.processes)
            try:
                count=0
                for n in (30,50,100):
                    for direction in ('U','D'):
                        started=time.monotonic();results=[];errors=[]
                        with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
                            futures=[pool.submit(lab.transfer,('apple.com','gs.apple.com')[i%2],direction,
                                262144,125000000,persistent=True,io_timeout=25) for i in range(n)]
                            for future in concurrent.futures.as_completed(futures):
                                try:results.append(future.result())
                                except Exception as error:
                                    errors.append({'type':type(error).__name__,'phase':getattr(error,'lab_phase','unknown'),
                                        'reason':sanitize_startup_log(str(error))[:256]})
                        count+=len(results)
                        report['rows'].append({'kind':kind,'concurrency':n,'direction':direction,'completed':len(results),
                            'errors':errors,'mock_dc_errors':list(lab.dc.errors),
                            'seconds':time.monotonic()-started,'backend':stats(lab.telemt.pid)})
                        write(Path(args.output),report)
                        if errors or lab.dc.errors or len(results)!=n:raise RuntimeError('native protocol transfer failed')
                lab.validate(count)
            finally:lab.close()
        complete=True
    finally:
        after=resources()
        report['resources_after']=after
        report['private_processes_closed']=all(p.poll() is not None for p in processes)
        report['oom_delta']={k:after['memory_events'][k]-before['memory_events'][k] for k in ('oom','oom_kill')}
        report['accepted']=complete and not any(report['oom_delta'].values()) and report['private_processes_closed']
        write(Path(args.output),report)


if __name__=='__main__':main()
