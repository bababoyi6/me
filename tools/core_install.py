"""Validate a pinned core archive and stage a fresh install; never update a node.

This source is embedded verbatim in the single-file Bash installer. All archive
bytes and members are verified before any executable is run or installed.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import stat
import subprocess
import sys
import tarfile
import tempfile


class InstallError(RuntimeError):
    pass


AUDIT_FILES = {'telemt','TELEMT-LICENSE.txt','NOTICE.txt','source.patch','build-provenance.json','manifest.json'}


def checksum(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024*1024), b''):
            value.update(block)
    return value.hexdigest()


def durable_file(path, content, mode):
    with Path(path).open('xb') as output:
        output.write(content)
        output.flush()
        os.fchmod(output.fileno(), mode)
        os.fsync(output.fileno())


def archive_payload(path, expected_archive, version, expected_binary='', audited=False):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 64*1024**2:
        raise InstallError('invalid archive type or size')
    if checksum(path) != expected_archive:
        raise InstallError('archive checksum mismatch')
    files, names, total = {}, set(), 0
    with tarfile.open(path, mode='r|gz') as archive:
        for number, member in enumerate(archive, 1):
            name = PurePosixPath(member.name)
            if (number > 32 or name.is_absolute() or '..' in name.parts or not name.parts
                    or str(name) in names or (not member.isfile() and not member.isdir())):
                raise InstallError('unsafe or duplicate archive member')
            names.add(str(name))
            if member.isdir():
                if audited:
                    raise InstallError('unexpected directory in audited archive')
                continue
            if not 0 < member.size <= 64*1024**2:
                raise InstallError('invalid member size')
            total += member.size
            if total > 64*1024**2:
                raise InstallError('archive payload exceeds limit')
            if audited and str(name) not in AUDIT_FILES:
                raise InstallError('unexpected audited archive member')
            content = archive.extractfile(member).read(member.size+1)
            if len(content) != member.size:
                raise InstallError('incomplete archive member')
            files[str(name)] = content
    binaries = [content for name, content in files.items() if PurePosixPath(name).name == 'telemt']
    if len(binaries) != 1:
        raise InstallError('archive must contain one telemt binary')
    binary = binaries[0]
    if expected_binary and hashlib.sha256(binary).hexdigest() != expected_binary:
        raise InstallError('binary checksum mismatch')
    if audited:
        if set(files) != AUDIT_FILES:
            raise InstallError('incomplete audited archive')
        if len(binary) < 64 or binary[:6] != b'\x7fELF\x02\x01' or binary[18:20] != b'\x3e\x00':
            raise InstallError('x86_64 ELF required')
        manifest = json.loads(files['manifest.json'])
        if (manifest.get('schema') != 1 or manifest.get('version') != version
                or manifest.get('architecture') != 'x86_64' or manifest.get('os') != 'linux'
                or manifest.get('libc') != 'gnu' or manifest.get('minimum_glibc') != '2.35'
                or manifest.get('unofficial') is not True
                or set(manifest.get('files', {})) != AUDIT_FILES-{'manifest.json'}):
            raise InstallError('audited manifest identity mismatch')
        for name, metadata in manifest['files'].items():
            if (metadata.get('sha256') != hashlib.sha256(files[name]).hexdigest()
                    or metadata.get('bytes') != len(files[name])
                    or metadata.get('mode') != ('0755' if name == 'telemt' else '0644')):
                raise InstallError('audited manifest member mismatch')
    return binary, {name:files[name] for name in AUDIT_FILES-{'telemt'} if name in files}


def fresh_install(archive, target, expected_archive, version, expected_binary='', audited=False):
    target = Path(target)
    parent = target.parent
    if not target.is_absolute() or target.resolve() != target or target.exists() or target.is_symlink():
        raise InstallError('fresh install refuses an existing core or symlink')
    parent_info = parent.lstat()
    if not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid != os.geteuid() or parent_info.st_mode & 0o022:
        raise InstallError('unsafe installation directory')
    binary, documents = archive_payload(archive, expected_archive, version, expected_binary, audited)
    identity = pwd.getpwnam('telemt')
    if identity.pw_uid == 0 or identity.pw_gid == 0:
        raise InstallError('unprivileged telemt identity required')
    with tempfile.TemporaryDirectory(prefix='.telemt-install-', dir=parent) as temporary:
        stage = Path(temporary)
        candidate = stage/'telemt'
        durable_file(candidate, binary, 0o755)
        stage.chmod(0o755)
        result = subprocess.run([str(candidate),'--version'], user=identity.pw_uid, group=identity.pw_gid,
                                extra_groups=[], cwd='/', capture_output=True, timeout=5, check=True)
        if result.stdout.strip() != ('telemt '+version).encode() or len(result.stdout) > 256:
            raise InstallError('candidate runtime version mismatch')
        if audited:
            legal = stage/'core-source-notice'
            legal.mkdir(mode=0o755)
            for name, content in documents.items():
                document = legal/name
                durable_file(document, content, 0o644)
            destination = parent/'core-source-notice'
            if destination.exists() or destination.is_symlink():
                raise InstallError('refusing to replace existing core notice')
            os.rename(legal, destination)
        # Exclusive link cannot clobber a concurrent or pre-existing executable.
        os.link(candidate, target)
        directory = os.open(parent, os.O_RDONLY|os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    return version


if __name__ == '__main__':
    try:
        if os.geteuid() != 0 or len(sys.argv) != 7:
            raise InstallError('root and exact install arguments required')
        archive, target, archive_sha, version, binary_sha, mode = sys.argv[1:]
        if mode not in ('official','audited'):
            raise InstallError('unknown archive mode')
        fresh_install(archive, target, archive_sha, version, binary_sha, mode == 'audited')
    except Exception as error:
        # No untrusted archive fields, paths, environment or service logs escape.
        print('核心安装验证失败：'+(str(error) if isinstance(error, InstallError) else type(error).__name__), file=sys.stderr)
        raise SystemExit(1)
