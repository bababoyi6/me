"""Small one-variable generator diagnostic; never a product performance claim."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import statistics
import tempfile

from e2e_lab import FullLab
from paired_performance import resources
from core_install import archive_payload
from stable_acceptance import PINS
from release_pins import RELEASES
from legacy_queue_analysis import NATIVE_SHA, UPSTREAM, BUILD_SOURCE


def main(args):
    if (os.geteuid()!=0 or os.environ.get('GITHUB_ACTIONS')!='true' or
            os.readlink('/proc/self/ns/net')==os.readlink('/proc/1/ns/net')):
        raise RuntimeError('private disposable CI only')
    before=resources()
    if before['cpu_max']!='200000 100000' or before['memory_max']!=str(512*1024**2) or before['swap_max']!='0':
        raise RuntimeError('registered diagnostic budget required')
    report={'source_commit':os.environ['TDH_SOURCE_COMMIT'],'complete':False,'rows':[],
        'scope':'loopback synthetic new FakeTLS+1KiB SHA/ACK; only client NODELAY changes; no throughput/client claim'}
    bundle=Path(args.native_directory);manifest=json.loads((bundle/'manifest.json').read_text())
    if (manifest['upstream_commit']!=UPSTREAM or manifest['manager_commit']!=BUILD_SOURCE or
            manifest['binaries']['balanced']['sha256']!=NATIVE_SHA):raise RuntimeError('native identity drift')
    try:
        with tempfile.TemporaryDirectory(prefix='tdh-audit-nodelay-') as folder:
            root=Path(folder)
            for variant,archive,pin in (('balanced2',args.balanced2_archive,PINS['balanced2']),
                                       ('official17',args.stable_archive,RELEASES['3.5.17']),
                                       ('native17',None,(None,NATIVE_SHA,manifest['binaries']['balanced']['version']))):
                payload=(bundle/'telemt-balanced').read_bytes() if archive is None else archive_payload(archive,pin[0],pin[2].removeprefix('telemt '),pin[1],False)[0]
                if hashlib.sha256(payload).hexdigest()!=pin[1]:raise RuntimeError('binary digest drift')
                target=root/variant;target.write_bytes(payload);target.chmod(0o755)
                lab=FullLab(target,experimental_binary=(pin[1],pin[2]),offline_lab=True)
                try:
                    lab.transfer('apple.com','D',1024,125000000,persistent=True)
                    for turn in range(4):
                        for enabled in ((False,True) if turn%2==0 else (True,False)):
                            rows=[lab.transfer(('apple.com','gs.apple.com')[i%2],'D',1024,125000000,
                                persistent=True,client_nodelay=enabled) for i in range(10)]
                            if any(row['client_nodelay']!=int(enabled) for row in rows):raise RuntimeError('requested client policy not applied')
                            report['rows'].append({'variant':variant,'round':turn+1,'binary_sha256':pin[1],
                                'client_nodelay':enabled,'transfers':rows,
                                'median_ms':statistics.median(row['elapsed_seconds']*1000 for row in rows)})
                    lab.validate(81)
                finally:lab.close()
            report['complete']=True
    finally:
        after=resources();report['resources_after']=after
        report['oom_delta']={key:after['memory_events'][key]-before['memory_events'][key] for key in ('oom','oom_kill')}
        Path(args.output).write_text(json.dumps(report,indent=2)+'\n')
    if not report['complete'] or any(report['oom_delta'].values()) or after['pids_current']!=1:
        raise RuntimeError('diagnostic incomplete or cleanup failed')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--balanced2-archive',required=True);parser.add_argument('--stable-archive',required=True)
    parser.add_argument('--native-directory',required=True);parser.add_argument('--output',required=True)
    signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
    main(parser.parse_args())
