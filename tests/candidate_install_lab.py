"""CI-only real candidate core installation and four tiny loopback transfers."""
import argparse
import json
import os
from pathlib import Path
import pwd
import signal
import stat
import sys
import tempfile

TOOLS = Path(__file__).resolve().parents[1]/'tools'
sys.path.insert(0,str(TOOLS))
from candidate_bundle import CORE_SHA256, VERSION
from core_install import fresh_install, InstallError, checksum
from e2e_lab import FullLab


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',required=True)
    parser.add_argument('--metadata',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    if os.geteuid()!=0 or os.environ.get('GITHUB_ACTIONS')!='true':
        parser.error('this integration check is confined to the ephemeral CI runner')
    identity=pwd.getpwnam('telemt')
    if identity.pw_uid==0 or identity.pw_gid==0:
        parser.error('unprivileged CI telemt account required')
    metadata=json.loads(Path(args.metadata).read_text())
    if metadata['version']!=VERSION or metadata['binary_sha256']!=CORE_SHA256:
        parser.error('pinned candidate metadata mismatch')
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    rows=[]
    with tempfile.TemporaryDirectory(prefix='tdh-candidate-install-') as folder:
        root=Path(folder)
        root.chmod(0o755)  # Public binary and synthetic fixtures only; allow the service UID to traverse.
        parent=root/'installed';parent.mkdir(mode=0o755)
        target=parent/'telemt'
        fresh_install(args.bundle,target,metadata['bundle_sha256'],VERSION,CORE_SHA256,True)
        if checksum(target)!=CORE_SHA256 or target.stat().st_uid!=0 or stat.S_IMODE(target.stat().st_mode)!=0o755:
            raise RuntimeError('installed binary identity or mode mismatch')
        legal=parent/'core-source-notice'
        documents={p.name for p in legal.iterdir()}
        if documents!={'TELEMT-LICENSE.txt','NOTICE.txt','source.patch','build-provenance.json','manifest.json'}:
            raise RuntimeError('source/license installation incomplete')
        installed_manifest=json.loads((legal/'manifest.json').read_text())
        for name,description in installed_manifest['files'].items():
            if name!='telemt' and (checksum(legal/name)!=description['sha256'] or stat.S_IMODE((legal/name).stat().st_mode)!=0o644):
                raise RuntimeError('installed source/license bytes or permissions changed')
        source=json.loads((legal/'build-provenance.json').read_text())
        if 'unsent_queue_bulk.rs' not in source['source_kit']:
            raise RuntimeError('complete candidate source replay kit missing')
        try:
            fresh_install(args.bundle,target,metadata['bundle_sha256'],VERSION,CORE_SHA256,True)
        except InstallError:
            pass
        else:
            raise RuntimeError('existing core was not rejected')
        bad=root/'corrupt.tar.gz'
        payload=bytearray(Path(args.bundle).read_bytes());payload[-1]^=1;bad.write_bytes(payload)
        rejected=root/'rejected';rejected.mkdir(mode=0o755)
        try:
            fresh_install(bad,rejected/'telemt',metadata['bundle_sha256'],VERSION,CORE_SHA256,True)
        except InstallError:
            pass
        else:
            raise RuntimeError('corrupt archive was not rejected')
        if list(rejected.iterdir()) or checksum(target)!=CORE_SHA256:
            raise RuntimeError('rejected install left artifacts or changed valid core')
        lab=FullLab(target,experimental_binary=(CORE_SHA256,'telemt '+VERSION))
        protocol_uid=lab.uid
        try:
            for domain in ('apple.com','gs.apple.com'):
                for direction in ('D','U'):
                    rows.append(lab.transfer(domain,direction,1024,1250000,fragment=True))
        finally:
            lab.close()
    result={'classification':'CI_CANDIDATE_CORE_INSTALL_CHAIN','version':VERSION,
            'binary_sha256':CORE_SHA256,'bundle_sha256':metadata['bundle_sha256'],
            'version_check_uid':identity.pw_uid,'version_check_gid':identity.pw_gid,
            'protocol_service_uid':protocol_uid,
            'real_fresh_install':True,'source_license_preserved':True,
            'existing_core_refused':True,'corruption_refused_before_staging':True,
            'loopback_sha256_verified_flows':len(rows),'payload_bytes':sum(r['bytes'] for r in rows),
            'rows':rows,'production_nodes_accessed':False,'default_installer_changed':False}
    Path(args.output).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='rows'}))


if __name__=='__main__':
    main()
