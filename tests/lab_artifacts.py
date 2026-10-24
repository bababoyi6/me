"""Private ownership ledger for temporary lab directories surviving worker SIGKILL."""
import json
import os
from pathlib import Path
import shutil
import stat
import uuid

MARKER='.tdh-lab-owner'


def private_file(path):
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_nlink!=1 or info.st_mode & 0o077:
        raise ValueError('ownership record is not a private regular file')
    return info


def registry_directory(path):
    path=Path(path)
    if path.is_symlink() or path.resolve()!=path:
        raise ValueError('unsafe ownership ledger path')
    path.mkdir(mode=0o700,exist_ok=True)
    info=path.stat()
    if info.st_uid!=os.geteuid() or info.st_mode & 0o077:
        raise ValueError('ownership ledger must be private')
    return path


def register_artifact(registry,directory):
    registry=registry_directory(registry)
    directory=Path(directory)
    if directory.parent!=Path('/tmp') or not directory.name.startswith('tdh-e2e-') or directory.is_symlink():
        raise ValueError('only private lab temporary directories can be registered')
    info=directory.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
        raise ValueError('temporary directory must be private')
    token=uuid.uuid4().hex
    fd=os.open(directory/MARKER,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as output:
        output.write(token)
    record={'path':str(directory),'device':info.st_dev,'inode':info.st_ino,'uid':info.st_uid,'token':token}
    fd=os.open(registry/(token+'.json'),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as output:
        json.dump(record,output)


def cleanup_artifacts(registry,processes_stopped):
    registry=Path(registry)
    result={'removed':[],'already_absent':[],'errors':[]}
    if not registry.exists():
        return result
    if not processes_stopped:
        result['errors'].append('experiment_processes_not_confirmed_stopped')
        return result
    try:
        registry=registry_directory(registry)
        records=sorted(registry.glob('*.json'))
        if len(records)>512:
            raise ValueError('too many ownership records')
        for record_path in records:
            try:
                if private_file(record_path).st_size>4096:
                    raise ValueError('oversized ownership record')
                record=json.loads(record_path.read_text())
                directory=Path(record['path'])
                if (directory.parent!=Path('/tmp') or not directory.name.startswith('tdh-e2e-') or
                        directory.is_symlink()):
                    raise ValueError('record outside lab temporary directory scope')
                if not directory.exists():
                    result['already_absent'].append(directory.name)
                    continue
                info=directory.stat()
                if (info.st_dev,info.st_ino,info.st_uid)!=(record['device'],record['inode'],record['uid']) or info.st_mode & 0o077:
                    raise ValueError('temporary directory identity changed')
                marker=directory/MARKER
                private_file(marker)
                if marker.read_text()!=record['token']:
                    raise ValueError('temporary directory ownership token changed')
                shutil.rmtree(directory)
                result['removed'].append(directory.name)
            except (OSError,ValueError,KeyError,TypeError):
                result['errors'].append(record_path.name)
    except (OSError,ValueError):
        result['errors'].append('ownership_ledger_unavailable')
    return result
