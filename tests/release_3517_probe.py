"""CI-only pinned release reconnaissance before changing the production selector."""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import tempfile
import time

from paired_performance import write
from core_install import archive_payload
from e2e_lab import FullLab
from telemt_probe import request, TOKEN

PINS={'amd64':'0570762ddcd79530d666b09e955b955bb52c9e134cc8b8ed1a0f50ad28d58a0e',
      'arm64':'f0d3d06d029f2b21dc66a44069bcc7bd20d32fcb34b5867c5250cee647785de6'}


def discovery_request(port,route,authenticated):
    # Schema discovery is larger than the ordinary health helper's 256KiB cap.
    connection=http.client.HTTPConnection('127.0.0.1',port,timeout=2)
    try:
        connection.request('GET',route,headers={'Authorization':'Bearer '+TOKEN} if authenticated else {})
        response=connection.getresponse();payload=response.read(2*1024**2+1)
        if len(payload)>2*1024**2:raise RuntimeError('discovery response exceeds 2MiB bound')
        json.loads(payload)
        return response.status,len(payload)
    finally:connection.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('amd64','arm64','output'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args()
    if os.environ.get('GITHUB_ACTIONS')!='true':raise RuntimeError('disposable CI only')
    report={'accepted':False,'source_commit':os.environ['GITHUB_SHA'],'version':'3.5.17',
        'upstream_commit':'66708d2866f3f3a4541c4fa67984d408e71a8af4','binaries':{},'api':[]}
    output=Path(args.output);lab=None
    try:
        with tempfile.TemporaryDirectory(prefix='tdh-3517-probe-') as directory:
            root=Path(directory)
            for arch in PINS:
                data,_=archive_payload(getattr(args,arch),PINS[arch],'3.5.17')
                if data[:6]!=b'\x7fELF\x02\x01' or data[18:20]!=({'amd64':b'\x3e\x00','arm64':b'\xb7\x00'}[arch]):
                    raise RuntimeError('unexpected release architecture')
                report['binaries'][arch]={'archive_sha256':PINS[arch],'binary_sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}
                if arch=='amd64':
                    binary=root/'telemt';binary.write_bytes(data);binary.chmod(0o755)
            pin=report['binaries']['amd64']['binary_sha256']
            lab=FullLab(binary,experimental_binary=(pin,'telemt 3.5.17'),offline_lab=True)
            report['transfers']=[lab.transfer(domain,direction,262144,125000000,persistent=True,io_timeout=25)
                for domain in ('apple.com','gs.apple.com') for direction in ('U','D')]
            lab.validate(4)
            deadline=time.monotonic()+15
            while True:
                status,data=request(lab.api,'GET','/v1/health/ready')
                if status==200 and data.get('data',{}).get('ready') is True:break
                if time.monotonic()>deadline:raise RuntimeError('new release readiness envelope incompatible')
                time.sleep(.2)
            report['readiness_compatible']=True
            original=lab.telemt_config.read_bytes()
            for route in ('/v1/config/state','/v1/capabilities','/v1/config/schema'):
                authorized,size=discovery_request(lab.api,route,True)
                anonymous,_=discovery_request(lab.api,route,False)
                report['api'].append({'route':route,'authorized':authorized,'anonymous':anonymous,'response_bytes':size})
                if authorized!=200 or anonymous!=401:raise RuntimeError('new API authorization incompatible')
            for method,route in (('POST','/v1/users'),('PATCH','/v1/config'),('POST','/v1/config/reload')):
                status,_=request(lab.api,method,route)
                report['api'].append({'route':route,'method':method,'read_only_status':status})
                if status!=403:raise RuntimeError('read-only API failed to refuse mutation')
            report['config_unchanged']=lab.telemt_config.read_bytes()==original
            if not report['config_unchanged']:raise RuntimeError('API mutated protected config')
            report['accepted']=True
    finally:
        if lab:lab.close()
        report['private_processes_closed']=lab is None or all(p.poll() is not None for p in lab.processes)
        write(output,report)


if __name__=='__main__':main()
