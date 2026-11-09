"""CI-only real audited fresh install, reproducible archive and full patch check."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from stable_core_bundle import bundle, PINS, VERSION, UPSTREAM
from core_install import archive_payload, fresh_install, InstallError


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',required=True);p.add_argument('--upstream',required=True);p.add_argument('--output',required=True)
    args=p.parse_args()
    if (os.geteuid()!=0 or os.environ.get('GITHUB_ACTIONS')!='true' or
        os.stat('/proc/self/ns/net').st_ino==os.stat('/proc/1/ns/net').st_ino):raise RuntimeError('private CI namespace required')
    upstream=Path(args.upstream).resolve()
    git=['git','-c','safe.directory='+str(upstream)]
    if subprocess.check_output([*git,'rev-parse','HEAD'],cwd=upstream,text=True).strip()!=UPSTREAM:raise RuntimeError('upstream mismatch')
    if subprocess.check_output([*git,'status','--porcelain'],cwd=upstream):raise RuntimeError('dirty upstream refused')
    output=Path(args.output);output.mkdir(mode=0o700)
    report={'source_commit':os.environ['GITHUB_SHA'],'accepted':False,'production_channel_enabled':False}
    try:
        archive=output/'telemt-3.5.17-tdh.balanced1-x86_64-linux-gnu.tar.gz'
        first=bundle(args.directory,archive);report['package']=first
        with tempfile.TemporaryDirectory(prefix='tdh-audit-bundle-') as directory:
            root=Path(directory);root.chmod(0o755)
            second=bundle(args.directory,root/'second.tar.gz')
            if first!=second or archive.read_bytes()!=(root/'second.tar.gz').read_bytes():raise RuntimeError('archive is not deterministic')
            report['two_archive_bytes_identical']=True
            _,documents=archive_payload(archive,digest(archive),VERSION,PINS['telemt-balanced'],True)
            source=root/'source.patch';source.write_bytes(documents['source.patch'])
            subprocess.run([*git,'apply','--check',str(source)],cwd=upstream,check=True,capture_output=True,timeout=10)
            report['complete_patch_applies_to_pinned_upstream']=True
            target=root/'installed';target.mkdir(mode=0o755)
            with patch('core_install.pwd.getpwnam',return_value=SimpleNamespace(pw_uid=65534,pw_gid=65534)):
                fresh_install(archive,target/'telemt',digest(archive),VERSION,PINS['telemt-balanced'],True)
                try:fresh_install(archive,target/'telemt',digest(archive),VERSION,PINS['telemt-balanced'],True)
                except InstallError:report['repeat_refused_existing_core_preserved']=True
                else:raise RuntimeError('repeat overwrote existing core')
            if digest(target/'telemt')!=PINS['telemt-balanced']:raise RuntimeError('fresh binary drift')
            notice=target/'core-source-notice'
            report['all_source_license_bytes_preserved']=all((notice/n).read_bytes()==b for n,b in documents.items())
            if not report['all_source_license_bytes_preserved']:raise RuntimeError('source/license content changed')
            report['unprivileged_exact_runtime_version_checked']=True
            report['accepted']=True
    finally:(output/'acceptance.json').write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
