"""Bounded real production download/install selection and Fake TLS smoke.

Archives are fetched from public pinned URLs by CI; only the function's curl
boundary is mapped to those exact files. Real ELF/hash checks, non-root version
execution and Telemt/HAProxy/protocol run. No throughput matrix or public DC.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import tempfile

from core_delivery_ci import install_case,PINS,digest
from e2e_lab import FullLab
from paired_performance import resources,write
from telemt_probe import request


def main(args):
    before=resources()
    if (os.geteuid()!=0 or os.environ.get('GITHUB_ACTIONS')!='true' or
        os.readlink('/proc/self/ns/net')==os.readlink('/proc/1/ns/net') or
        before['cpu_max']!='100000 100000' or before['memory_max']!=str(512*1024**2) or before['swap_max']!='0'):
        raise RuntimeError('owned bounded CI namespace only')
    report={'source_commit':os.environ['TDH_SOURCE_COMMIT'],'accepted':False,'cases':[],
        'scope':'actual production download/install selection; exact released binaries; synthetic DC/identities; no throughput claim',
        'network_boundary_substituted':True,'channels':{},'resources_before':before}
    stage='setup';lab=None
    try:
        with tempfile.TemporaryDirectory(prefix='tdh-fresh-core-') as name:
            root=Path(name);root.chmod(0o755)
            for channel,archive in (('balanced2',Path(args.balanced2_archive)),('official',Path(args.official_archive))):
                stage='install-'+channel
                binary,record=install_case(root/channel,channel,archive)
                report['cases'].append(record)
                stage='protocol-'+channel
                lab=FullLab(binary,offline_lab=True,experimental_binary=(PINS[channel][2],'telemt '+PINS[channel][0]))
                current={'version':lab.version,'binary_sha256':digest(binary),
                    'unauthenticated_api_status':request(lab.api,'GET','/v1/users',False)[0],
                    'readonly_write_status':request(lab.api,'POST','/v1/users')[0],'rows':[]}
                for domain in ('apple.com','gs.apple.com'):
                    for direction in ('U','D'):
                        current['rows'].append(lab.transfer(domain,direction,4096,1000000,persistent=True,fragment=True))
                with ThreadPoolExecutor(max_workers=4) as pool:
                    futures=[pool.submit(lab.transfer,('apple.com','gs.apple.com')[i%2],('U','D')[i//2%2],
                        262144,12500000,persistent=True) for i in range(16)]
                    current['rows'] += [f.result(timeout=20) for f in futures]
                lab.validate(20)
                with lab.dc.error_lock:
                    current['mock_dc']={'completed':lab.dc.completed,'errors':list(lab.dc.errors)}
                current['config_unchanged']=hashlib.sha256(lab.telemt_config.read_bytes()).hexdigest()==lab.config_hash
                lab.close();current['processes_closed']=all(p.poll() is not None for p in lab.processes);lab=None
                current['accepted']=(current['unauthenticated_api_status']==401 and current['readonly_write_status']==403 and
                    current['config_unchanged'] and current['processes_closed'] and len(current['rows'])==20 and
                    all(row['sha256_verified'] for row in current['rows']))
                report['channels'][channel]=current
                if not current['accepted']:raise RuntimeError('functional/auth/config acceptance failed')
                stage='refusal-'+channel
                bad=root/(channel+'-corrupt.tar.gz');data=bytearray(archive.read_bytes());data[-1]^=1;bad.write_bytes(data)
                for kind,item,existing in (('corrupt',bad,False),('existing',archive,True)):
                    _,record=install_case(root/(channel+'-'+kind),channel,item,existing=existing,accepted=False)
                    report['cases'].append(record)
            report['accepted']=all(c['accepted'] for c in report['channels'].values()) and len(report['cases'])==6
    except BaseException as exc:
        report['failure']={'stage':stage,'type':type(exc).__name__}
        raise
    finally:
        if lab is not None:lab.close()
        report['resources_after']=resources()
        report['oom_delta']={key:report['resources_after']['memory_events'][key]-before['memory_events'][key] for key in ('oom','oom_kill')}
        report['accepted']=(report['accepted'] and not any(report['oom_delta'].values()) and report['resources_after']['pids_current']==1)
        write(args.output,report)
    return 0 if report['accepted'] else 1


if __name__=='__main__':
    import signal
    p=argparse.ArgumentParser();p.add_argument('--balanced2-archive',required=True);p.add_argument('--official-archive',required=True)
    p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
    raise SystemExit(main(a))
