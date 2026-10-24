"""CI-only real installer download boundary and staged core delivery qualification."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import tempfile

from test_core_selection import PINS

REPO=Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def download_fixture(arguments):
    # Only the network fetch is substituted. All production archive/version/install code runs.
    if os.environ.get('GITHUB_ACTIONS')!='true':
        raise RuntimeError('download fixture is CI-only')
    if (arguments[0]!='-q' or arguments[arguments.index('--proto')+1]!='=https' or
            arguments[arguments.index('-o')-1]!=os.environ['TDH_DELIVERY_URL']):
        raise RuntimeError('unexpected production download request')
    Path(os.environ['TDH_DELIVERY_FETCH']).write_text(json.dumps(arguments)+'\n')
    shutil.copyfile(os.environ['TDH_DELIVERY_ARCHIVE'],arguments[arguments.index('-o')+1])


def install_case(folder,channel,archive,*,existing=False,accepted=True):
    folder.mkdir(mode=0o755)
    folder.chmod(0o755)  # umask 077 must not block the real service UID's traversal.
    config=folder/'config';config.mkdir(mode=0o700)
    target=folder/'bin/telemt';target.parent.mkdir(mode=0o755)
    target.parent.chmod(0o755)
    identity=pwd.getpwnam('telemt')
    subprocess.run(['test','-x',str(target.parent)],user=identity.pw_uid,group=identity.pw_gid,
                   extra_groups=[],check=True,timeout=3)
    if existing:
        target.write_bytes(b'EXISTING_CORE_MUST_REMAIN')
    version,archive_sha,core_sha=PINS[channel]
    asset=f'telemt-{version.replace("+","-")}-x86_64-linux-gnu.tar.gz'
    url=f'https://github.com/bababoyi6/me/releases/download/telemt-core-{version.replace("+","-")}/{asset}'
    env=dict(os.environ,TDH_BASE=str(config),TDH_BIN=str(target),TDH_DELIVERY_SCRIPT=str(REPO/'telemt-dual-hop.sh'),
             TDH_DELIVERY_FIXTURE=str(Path(__file__).resolve()),TDH_DELIVERY_ARCHIVE=str(archive),
             TDH_DELIVERY_URL=url,TDH_DELIVERY_FETCH=str(folder/'fetch.json'))
    env.pop('TDH_NEW_INSTALL_CORE',None)
    if channel=='audit2':
        env['TDH_NEW_INSTALL_CORE']='audit2'
    body='''set -Eeuo pipefail
TDH_SOURCE_ONLY=1 source "$TDH_DELIVERY_SCRIPT"
curl() { python3 "$TDH_DELIVERY_FIXTURE" --download-fixture "$@"; }
download_telemt
telemt_binary_is_expected
'''
    result=subprocess.run(['bash','-c',body],env=env,capture_output=True,text=True,timeout=25)
    if (result.returncode==0)!=accepted:
        raise RuntimeError('production installer result mismatch: '+result.stderr[-1000:])
    if existing:
        if target.read_bytes()!=b'EXISTING_CORE_MUST_REMAIN' or (folder/'fetch.json').exists():
            raise RuntimeError('existing target changed or downloaded before refusal')
    elif accepted:
        if digest(target)!=core_sha:
            raise RuntimeError('production installer installed unexpected core')
        documents=target.parent/'core-source-notice'
        manifest=json.loads((documents/'manifest.json').read_text())
        for name,record in manifest['files'].items():
            if name!='telemt' and digest(documents/name)!=record['sha256']:
                raise RuntimeError('production installer lost source/license bytes')
    elif target.exists() or (target.parent/'core-source-notice').exists():
        raise RuntimeError('rejected install left a core or source notice')
    if list(config.iterdir()) or list(target.parent.glob('.telemt-install-*')):
        raise RuntimeError('production installer left download or staging files')
    return target,{'channel':channel,'accepted':accepted,'existing_preserved':existing,
                   'archive_sha256':archive_sha,'binary_sha256':core_sha,'url':url}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True)
    parser.add_argument('--balanced2-archive',required=True)
    parser.add_argument('--audit2-archive',required=True)
    args=parser.parse_args()
    root=Path(args.root)
    if (os.geteuid()!=0 or os.environ.get('GITHUB_ACTIONS')!='true' or not root.is_absolute() or
            root.resolve()!=root or root.exists() or root.is_symlink() or
            root.parent!=Path(os.environ['RUNNER_TEMP']).resolve() or root.name!='tdh-audit-core-delivery'):
        parser.error('requires a fresh private directory on the ephemeral CI runner')
    os.umask(0o077)
    identity=pwd.getpwnam('telemt')
    if identity.pw_uid==0 or identity.pw_gid==0:
        parser.error('unprivileged CI telemt account required')
    archives={'balanced2':Path(args.balanced2_archive),'audit2':Path(args.audit2_archive)}
    for channel,archive in archives.items():
        if digest(archive)!=PINS[channel][1]:
            parser.error('pinned existing archive mismatch')
    root.mkdir(mode=0o700);(root/'results').mkdir(mode=0o700)
    records=[]
    report={'classification':'CI_PRODUCTION_INSTALL_FUNCTION',
            'service_version_uid':identity.pw_uid,'cases':records,'network_boundary_substituted':True,
            'published_release_download_tested':False,'host_configuration_modified':False,'accepted':False}
    try:
        # RUNNER_TEMP itself may have private ancestors; never chmod runner-owned directories.
        with tempfile.TemporaryDirectory(prefix='tdh-delivery-install-',dir='/tmp') as folder:
            stage=Path(folder);stage.chmod(0o755)  # Public binaries only; version checks run as telemt.
            for channel,archive in archives.items():
                installed,record=install_case(stage/channel,channel,archive)
                records.append(record)
                shutil.copyfile(installed,root/('telemt-'+channel))
                (root/('telemt-'+channel)).chmod(0o755)
                version,_,sha=PINS[channel]
                (root/(channel+'-build.json')).write_text(json.dumps({'sha256':sha,'version':'telemt '+version})+'\n')
            bad=stage/'corrupt.tar.gz'
            content=bytearray(archives['balanced2'].read_bytes());content[-1]^=1;bad.write_bytes(content)
            for name,archive,existing in (('corrupt',bad,False),('existing',archives['balanced2'],True)):
                _,record=install_case(stage/name,'balanced2',archive,existing=existing,accepted=False)
                records.append(record)
        report['accepted']=True
    except Exception as exc:
        report['error_type']=type(exc).__name__
        raise
    finally:
        (root/'results/installer-delivery.json').write_text(json.dumps(report,indent=2)+'\n')
    os.execv(sys.executable,[sys.executable,'-B',str(REPO/'tools/lab_runner.py'),'--root',str(root),
                            '--profile','delivery','--rtt','100','--execute'])


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--download-fixture':
        download_fixture(sys.argv[2:])
    else:
        main()
