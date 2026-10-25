"""Failure fuse for one fresh endurance root; never deletes an unowned namespace."""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from network_lab import exclusive_lock,GLOBAL_LOCK,RESERVED_NAMES
from lab_artifacts import cleanup_artifacts


def cleanup(root):
    assert os.geteuid()==0 and root.resolve()==root and not root.is_symlink()
    assert re.fullmatch(r'tdh-audit-endurance-[0-9a-f]{12}',root.name) or (
        os.environ.get('GITHUB_ACTIONS')=='true' and root.name=='tdh-audit-endurance-pilot')
    info=root.stat();assert info.st_uid==0 and not info.st_mode&0o077
    report={'complete':False,'namespaces_removed':[],'keys_removed':0,'errors':[]}
    # Stop the owned worker before requesting its network lock; an active
    # worker holds the lock for its entire life, including normal cleanup.
    manifests=list(root.glob('run-tdh-audit-*/manifest.json'))
    for path in manifests:
        assert not path.is_symlink() and path.stat().st_size<4*1024**2
        unit=path.parent.name.removeprefix('run-')
        assert re.fullmatch(r'tdh-audit-[0-9a-f]{12}',unit)
        stopped=subprocess.run(['systemctl','stop',unit],capture_output=True,timeout=30)
        assert stopped.returncode in (0,5), 'owned unit could not stop'
        check=subprocess.run(['systemctl','show',unit,'--property=ControlGroup','--value'],capture_output=True,text=True,timeout=5)
        group=check.stdout.strip()
        if group:
            assert group.startswith('/system.slice/'+unit)
            processes=Path('/sys/fs/cgroup'+group)/'cgroup.procs'
            assert not processes.exists() or not processes.read_text().strip(), 'worker still live'
    with exclusive_lock(GLOBAL_LOCK):
        ledger=root/'namespace-owner.json'
        owned=json.loads(ledger.read_text()) if ledger.exists() else {}
        assert set(owned).issubset(RESERVED_NAMES)
        for name,identity in owned.items():
            path=Path('/run/netns')/name
            if not path.exists():continue
            value=path.stat()
            if (value.st_dev,value.st_ino)!=(identity['device'],identity['inode']):
                report['errors'].append('namespace_identity_changed:'+name);continue
            # No surviving process may retain the mount we are about to remove.
            pids=subprocess.run(['ip','netns','pids',name],check=True,capture_output=True,text=True,timeout=5).stdout.strip()
            if pids:
                report['errors'].append('namespace_still_used:'+name);continue
            subprocess.run(['ip','netns','del',name],check=True,capture_output=True,timeout=5)
            report['namespaces_removed'].append(name)
        for path in root.glob('*.key'):
            value=path.lstat()
            assert stat.S_ISREG(value.st_mode) and value.st_uid==0 and not value.st_mode&0o077
            path.unlink();report['keys_removed']+=1
        remaining=subprocess.run(['ip','netns','list'],check=True,capture_output=True,text=True,timeout=5).stdout
        if any(line.split()[0] in RESERVED_NAMES for line in remaining.splitlines()):
            report['errors'].append('reserved_namespace_remains')
        report['temporary_artifacts']=cleanup_artifacts(root/'artifact-registry',not report['errors'])
        if report['temporary_artifacts']['errors']:
            report['errors'].append('private_temporary_artifact_cleanup')
    report['complete']=not report['errors']
    destination=root/'fuse-cleanup.json'
    descriptor=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(descriptor,'w') as output:json.dump(report,output,indent=2)
    assert report['complete'],'owned cleanup incomplete'


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,required=True)
    cleanup(parser.parse_args().root)
