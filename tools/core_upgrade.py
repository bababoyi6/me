"""Pinned backend-only upgrade adapter for CoreTransaction.

Core replacement preserves the managed schema-5 configuration byte-for-byte.
A separate explicit connect-policy transaction edits three managed defaults only.
The caller holds the manager lock and explicitly acknowledges a maintenance restart.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

from core_install import InstallError, archive_payload, durable_file
from core_transaction import CoreError, CoreTransaction, atomic_copy, digest, safe_directory, safe_file, sync_directory


class ConfigAccessMigration:
    """One reversible 0710 -> 0750 parent-mode migration, with an immutable record.

    No ownership, file bytes or child permissions change. The manager holds its
    node lock. A crash before/after chmod is resumed from the same private record.
    """
    def __init__(self, base, directory, binary, group):
        self.base, self.binary = Path(base), Path(binary)
        self.record = Path(directory)/'config-access.json'
        self.group = group

    def validate(self):
        safe_directory(self.base)
        safe_directory(self.record.parent)
        if self.record.parent.stat().st_mode & 0o077:
            raise CoreError('config access record directory must be private')
        info = self.base.stat()
        if (info.st_gid != self.group or self.group == 0 or
                stat.S_IMODE(info.st_mode) not in (0o710, 0o750)):
            raise CoreError('custom config parent permissions; review required')
        if self.record.exists() or self.record.is_symlink():
            safe_file(self.record)
            if self.record.stat().st_mode & 0o077 or self.record.stat().st_size > 4096:
                raise CoreError('unsafe config access record')
            value = json.loads(self.record.read_text())
            if (value.get('schema') != 1 or value.get('path') != str(self.base) or
                    value.get('uid') != info.st_uid or value.get('gid') != self.group or
                    value.get('before') != 0o710 or value.get('after') != 0o750 or
                    not re.fullmatch('[a-f0-9]{64}', value.get('legacy_binary_sha256', ''))):
                raise CoreError('invalid config access record')
            return value
        return None

    def set_mode(self, mode):
        descriptor = os.open(self.base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(descriptor)
            if (info.st_ino, info.st_dev) != (self.base.stat().st_ino, self.base.stat().st_dev):
                raise CoreError('config directory changed during migration')
            os.fchmod(descriptor, mode)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def prepare(self):
        value = self.validate()
        if stat.S_IMODE(self.base.stat().st_mode) == 0o750:
            return False
        if value is None:
            value = {'schema': 1, 'path': str(self.base), 'uid': self.base.stat().st_uid,
                     'gid': self.group, 'before': 0o710, 'after': 0o750,
                     'legacy_binary_sha256': digest(self.binary)}
            with tempfile.TemporaryDirectory(prefix='.config-access-', dir=self.record.parent) as name:
                staged = Path(name)/'record'
                durable_file(staged, (json.dumps(value, sort_keys=True)+'\n').encode(), 0o600)
                os.link(staged, self.record)
            sync_directory(self.record.parent)
        elif digest(self.binary) != value['legacy_binary_sha256']:
            raise CoreError('config access baseline changed; review required')
        self.set_mode(0o750)
        return True

    def restore(self):
        value = self.validate()
        # Never restrict a newer core restored from a prior committed transaction.
        if value is not None and digest(self.binary) == value['legacy_binary_sha256']:
            self.set_mode(0o710)
            return True
        return False


class BackendService:
    def __init__(self, binary, config, work, token_file, api_port=19091,
                 unit='telemt-dual-hop-telemt.service', user='telemt', migrate_access=False):
        self.binary, self.config, self.work = Path(binary), Path(config), Path(work)
        self.token_file, self.api_port = Path(token_file), api_port
        self.unit = unit
        self.migrate_access = migrate_access
        identity = pwd.getpwnam(user)
        if identity.pw_uid == 0 or identity.pw_gid == 0:
            raise CoreError('unprivileged telemt identity required')
        self.identity = dict(user=identity.pw_uid, group=identity.pw_gid, extra_groups=[])

    def version(self, binary):
        result = subprocess.run([str(binary), '--version'], capture_output=True,
                                timeout=5, check=True, cwd='/', **self.identity)
        return result.stdout.decode('ascii').strip()

    def validate_candidate(self, binary):
        if not self.running_binary_matches():
            raise CoreError('service process does not run the expected current core')
        # Upstream's bounded healthcheck parses the old strict config without
        # launching a new listener or generation, then probes the current API.
        result = subprocess.run([str(binary), 'healthcheck', '--mode', 'ready', str(self.config)],
                                cwd=self.work, capture_output=True, timeout=12, **self.identity)
        if result.returncode:
            raise CoreError('candidate rejects old config or current backend is not ready')

    def running_binary_matches(self):
        result = subprocess.run(['systemctl', 'show', self.unit,
                                 '--property=MainPID', '--value'], capture_output=True, text=True, timeout=5, check=True)
        pid = result.stdout.strip()
        if not pid.isdecimal() or int(pid) <= 0:
            return False
        try:
            return digest(Path('/proc')/pid/'exe') == digest(self.binary)
        except OSError:
            return False

    def stop(self):
        subprocess.run(['systemctl', 'stop', self.unit],
                       check=True, capture_output=True, timeout=40)

    def start(self):
        subprocess.run(['systemctl', 'start', self.unit],
                       check=True, capture_output=True, timeout=40)

    def ready(self):
        # Control-plane readiness + actual process image; protocol is tested separately.
        import http.client
        token = self.token_file.read_text(encoding='ascii').strip()
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            connection = http.client.HTTPConnection('127.0.0.1', self.api_port, timeout=2)
            try:
                connection.request('GET', '/v1/health/ready', headers={'Authorization': 'Bearer '+token})
                response = connection.getresponse()
                raw = response.read(16385)
                data = json.loads(raw) if len(raw) <= 16384 else None
                if (response.status == 200 and isinstance(data, dict) and data.get('ok') is True
                        and isinstance(data.get('data'), dict) and data['data'].get('ready') is True
                        and self.running_binary_matches()):
                    return True
            except (OSError, ValueError):
                pass
            finally:
                connection.close()
            time.sleep(1)
        return False


class HealthService:
    def __init__(self, bind):
        import ipaddress
        self.bind = str(ipaddress.IPv4Address(bind))
    def version(self, path):
        import ast
        ast.parse(Path(path).read_text(encoding='utf-8'))
        return digest(path)
    def stop(self):
        subprocess.run(['systemctl', 'stop', 'telemt-dual-hop-health-agent.service'],
                       check=True, capture_output=True, timeout=20)
    def start(self):
        subprocess.run(['systemctl', 'start', 'telemt-dual-hop-health-agent.service'],
                       check=True, capture_output=True, timeout=20)
    def ready(self):
        import socket
        deadline = time.monotonic()+45
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((self.bind, 19101), timeout=2) as connection:
                    connection.settimeout(2)
                    if connection.recv(16).strip() == b'up':
                        return True
            except OSError:
                pass
            time.sleep(1)
        return False


def refresh_health(base, bind, rollback_only=False, *, target=None, service=None):
    directory = Path(base)/'health-upgrade'
    if not directory.exists():
        directory.mkdir(mode=0o700)
    target = Path(target or '/usr/local/lib/telemt-dual-hop/health_agent.py')
    transaction = CoreTransaction(target, directory, service or HealthService(bind))
    if rollback_only:
        return transaction.rollback()
    source = globals().get('HEALTH_AGENT_SOURCE')
    if source is None:
        source = Path(__file__).with_name('health_agent.py').read_text(encoding='utf-8')
    with tempfile.TemporaryDirectory(prefix='.health-candidate-', dir=target.parent) as temp:
        candidate = Path(temp)/'health_agent.py'
        durable_file(candidate, source.encode('utf-8'), 0o755)
        expected = digest(candidate)
        return transaction.activate(candidate, expected, expected, digest(target))


MAX_CONNECT_CONFIG_BYTES = 1024 * 1024
MAX_CONNECT_POLICY_BACKUPS = 8


def bounded_connect_config(content):
    """Edit three managed defaults only; custom values/layouts fail closed."""
    if len(content) > MAX_CONNECT_CONFIG_BYTES:
        raise CoreError('connect config exceeds 1MiB bound')
    text = content.decode('utf8')
    if any(marker in text for marker in ('\x00', '\ufeff', '\"\"\"', "'''")):
        raise CoreError('custom TOML layout; review required')
    lines = text.splitlines(keepends=True)
    headers = [i for i,line in enumerate(lines) if re.fullmatch(r'\[general\][ \t]*(?:#[^\r\n]*)?\r?\n?', line)]
    if len(headers) != 1:
        raise CoreError('one managed general table required')
    start = headers[0]+1
    end = next((i for i in range(start,len(lines)) if lines[i].lstrip().startswith('[')),len(lines))
    defaults = {'tg_connect':10, 'upstream_connect_retry_attempts':2, 'upstream_connect_budget_ms':3000}
    desired = {'tg_connect':4, 'upstream_connect_retry_attempts':2, 'upstream_connect_budget_ms':9000}
    seen = set()
    for i in range(start,end):
        for key in desired:
            if not re.match(r'\s*'+key+r'\s*=', lines[i]): continue
            match = re.fullmatch(r'(\s*'+key+r'\s*=\s*)([0-9]+)([ \t]*(?:#[^\r\n]*)?)(\r?\n)?', lines[i])
            if key in seen or match is None or int(match[2]) not in (defaults[key],desired[key]):
                raise CoreError('custom connect settings; review required')
            seen.add(key)
            lines[i] = match[1]+str(desired[key])+match[3]+(match[4] or '')
    newline = '\r\n' if lines[start-1].endswith('\r\n') else '\n'
    if not lines[start-1].endswith('\n'): lines[start-1] += newline
    lines[start:start] = [f'{key} = {value}{newline}' for key,value in desired.items() if key not in seen]
    return ''.join(lines).encode('utf8')


class ConnectPolicyService:
    def __init__(self, backend): self.backend = backend
    def version(self, path): return digest(path)
    def stop(self): return self.backend.stop()
    def start(self): return self.backend.start()
    def ready(self): return self.backend.ready()


def refresh_connect_policy(base, service, rollback_only=False):
    target = service.config
    safe_directory(Path(base))
    safe_file(target)
    info = target.stat()
    if stat.S_IMODE(info.st_mode) != 0o640 or info.st_gid != service.identity['group']:
        raise CoreError('custom config ownership or mode; review required')
    if not rollback_only and info.st_size > MAX_CONNECT_CONFIG_BYTES:
        raise CoreError('connect config exceeds 1MiB bound')
    directory = Path(base)/'connect-policy'
    if not directory.exists(): directory.mkdir(mode=0o700)
    transaction = CoreTransaction(target, directory, ConnectPolicyService(service),
                                  file_mode=0o640, file_owner=(info.st_uid,info.st_gid))
    if rollback_only: return transaction.rollback()
    with target.open('rb') as source:
        original = source.read(MAX_CONNECT_CONFIG_BYTES + 1)
    baseline = hashlib.sha256(original).hexdigest()
    content = bounded_connect_config(original)
    expected = hashlib.sha256(content).hexdigest()
    if transaction.journal.exists() and transaction.load()['phase'] not in ('committed','recovered'):
        raise CoreError('unfinished connect transaction; run connect-rollback first')
    if digest(target) != baseline:
        raise CoreError('config changed while preparing connect policy')
    if expected == baseline:
        if not service.ready(): raise CoreError('backend is not ready')
        return 'unchanged'
    if (len(list(directory.glob('core.previous-*'))) >= MAX_CONNECT_POLICY_BACKUPS
            and not (directory/('core.previous-'+baseline)).exists()):
        raise CoreError('connect backup limit reached; privately archive old backups before maintenance')
    with tempfile.TemporaryDirectory(prefix='.connect-candidate-',dir=target.parent) as name:
        stage=Path(name)
        os.chown(stage,info.st_uid,info.st_gid); stage.chmod(0o750)
        candidate=stage/'telemt.toml'
        durable_file(candidate,content,0o640)
        os.chown(candidate,info.st_uid,info.st_gid)
        # Parse using the installed core before stopping the current service.
        result=subprocess.run([str(service.binary),'healthcheck','--mode','ready',str(candidate)],
            cwd=service.work,capture_output=True,timeout=12,**service.identity)
        if result.returncode or not service.running_binary_matches():
            raise CoreError('connect candidate validation or running core identity failed')
        return transaction.activate(candidate,expected,expected,baseline)


def protected_files(roots):
    files = {}
    for root in map(Path, roots):
        if root.is_symlink() or root.resolve() != root:
            raise CoreError('protected path contains a symlink')
        candidates = [root] if root.is_file() else sorted(root.rglob('*')) if root.is_dir() else []
        if not root.exists():
            raise CoreError('required protected path is missing')
        if not root.is_file() and not root.is_dir():
            raise CoreError('protected root must be a regular file or directory')
        if root.is_dir():
            safe_directory(root)
        for path in candidates:
            if path.is_symlink():
                raise CoreError('protected tree contains a symlink')
            if path.is_dir():
                safe_directory(path)
                continue
            safe_file(path)
            if path.stat().st_size > 16*1024**2 or len(files) >= 128:
                raise CoreError('protected state exceeds backup bounds')
            files[str(path)] = {'sha256': digest(path), 'mode': stat.S_IMODE(path.stat().st_mode)}
    if sum(Path(name).stat().st_size for name in files) > 32*1024**2:
        raise CoreError('protected backup exceeds total byte budget')
    return files


def save_snapshot(directory, roots):
    """Only hashes and fixed paths in the manifest; all copied secrets stay 0600."""
    files = protected_files(roots)
    folder = Path(tempfile.mkdtemp(prefix='snapshot-', dir=directory))
    try:
        for index, (name, metadata) in enumerate(sorted(files.items())):
            metadata['copy'] = str(index)
            atomic_copy(name, folder/str(index), 0o600, metadata['sha256'])
        durable_file(folder/'manifest.json', (json.dumps(files, sort_keys=True)+'\n').encode(), 0o600)
        sync_directory(folder)
        sync_directory(directory)
        return folder
    except BaseException:
        shutil.rmtree(folder)
        raise


def unchanged_snapshot(folder, roots):
    safe_directory(folder)
    safe_file(Path(folder)/'manifest.json')
    files = json.loads((Path(folder)/'manifest.json').read_text())
    current = protected_files(roots)
    expected = {name: {k: row[k] for k in ('sha256', 'mode')} for name, row in files.items()}
    if current != expected:
        raise CoreError('protected configuration changed; manual review required before rollback')
    for row in files.values():
        if not re.fullmatch(r'[0-9]{1,3}', row['copy']):
            raise CoreError('invalid snapshot member')
        source = Path(folder)/row['copy']
        safe_file(source)
        if digest(source) != row['sha256']:
            raise CoreError('protected backup checksum mismatch')


def preserve_audited_archive(directory, archive, archive_sha, binary_sha):
    """Keep exact source/license bytes without changing rollback-protected files."""
    if not re.fullmatch(r'[a-f0-9]{64}', binary_sha):
        raise CoreError('invalid audited core identity')
    directory=Path(directory)
    safe_directory(directory)
    if directory.stat().st_mode & 0o077:
        raise CoreError('audited archive storage must be private')
    target=directory/('audited-core-'+binary_sha+'.tar.gz')
    if target.exists() or target.is_symlink():
        safe_file(target)
        if stat.S_IMODE(target.stat().st_mode)!=0o600 or digest(target)!=archive_sha:
            raise CoreError('audited archive cache changed; review required')
    else:
        if len(list(directory.glob('audited-core-*.tar.gz')))>=8:
            raise CoreError('audited archive retention limit reached; archive old notices first')
        atomic_copy(archive,target,0o600,archive_sha)
    return target


def upgrade(binary, directory, archive, archive_sha, binary_sha, version, roots, service, *, audited=False):
    safe_directory(directory)
    if Path(directory).stat().st_mode & 0o077:
        raise CoreError('transaction storage must be private')
    safe_file(binary)
    tx = CoreTransaction(binary, directory, service)
    if tx.journal.exists() or tx.journal.is_symlink():
        previous = tx.load()
        if previous['phase'] in ('prepared', 'switching'):
            raise CoreError('unfinished transaction; run core-rollback first')
    old = digest(binary)
    if old == binary_sha:
        if not service.ready():
            raise CoreError('installed target core is not ready; diagnose or recover it first')
        return 'unchanged'
    if len(list(Path(directory).glob('snapshot-*'))) >= 8:
        raise CoreError('snapshot retention limit reached; archive old backups before upgrading')
    payload, _ = archive_payload(archive, archive_sha, version, binary_sha, audited)
    if audited:
        preserve_audited_archive(directory,archive,archive_sha,binary_sha)
    # Stage on an executable root-owned filesystem, readable by telemt only
    # through the temporary 0755 directory; no credentials are staged here.
    with tempfile.TemporaryDirectory(prefix='.core-candidate-', dir=Path(binary).parent) as temp:
        stage = Path(temp)
        candidate = stage/'telemt'
        durable_file(candidate, payload, 0o755)
        stage.chmod(0o755)
        snapshot = save_snapshot(directory, roots)
        access = (ConfigAccessMigration(service.config.parent.parent, directory, binary,
                                       service.identity['group']) if getattr(service, 'migrate_access', False) else None)
        migrated = False
        try:
            if access is not None:
                if stat.S_IMODE(access.base.stat().st_mode) == 0o710:
                    backup = Path(directory)/('core.previous-'+old)
                    if backup.exists() or backup.is_symlink():
                        safe_file(backup)
                    # Config/key/unit snapshot and original binary are durable
                    # before the first permissions change, even before preflight.
                    atomic_copy(binary, backup, 0o600, old)
                migrated = access.prepare()
            service.validate_candidate(candidate)
            # Bind the snapshot to the exact durable journal, including recovery.
            result = tx.activate(candidate, binary_sha, 'telemt '+version, old, snapshot=snapshot.name)
            unchanged_snapshot(snapshot, roots)
            return result
        except BaseException:
            if migrated:
                access.restore()
            # A preflight failure must not consume the finite snapshot budget.
            # Keep any snapshot referenced by the durable recovery journal.
            if not tx.journal.exists() or tx.load().get('snapshot') != snapshot.name:
                shutil.rmtree(snapshot)
            raise


def rollback(binary, directory, roots, service):
    transaction = CoreTransaction(binary, directory, service)
    access = (ConfigAccessMigration(service.config.parent.parent, directory, binary,
                                   service.identity['group']) if getattr(service, 'migrate_access', False) else None)
    if not transaction.journal.exists() and access is not None and access.record.exists():
        if not access.restore():
            raise CoreError('config access recovery baseline changed; review required')
        return 'access-restored'
    value = transaction.load()
    name = value.get('snapshot', '')
    if not re.fullmatch(r'snapshot-[a-z0-9_]{8}', name):
        raise CoreError('missing or invalid transaction snapshot')
    unchanged_snapshot(Path(directory)/name, roots)
    result = transaction.rollback()
    if access is not None:
        access.restore()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('upgrade', 'rollback', 'refresh-health', 'rollback-health', 'refresh-connect', 'rollback-connect'))
    parser.add_argument('--binary', required=True)
    parser.add_argument('--directory', required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--work', required=True)
    parser.add_argument('--token-file', required=True)
    parser.add_argument('--protect', action='append', required=True)
    parser.add_argument('--archive')
    parser.add_argument('--archive-sha')
    parser.add_argument('--binary-sha')
    parser.add_argument('--version')
    parser.add_argument('--audited',action='store_true')
    parser.add_argument('--bind')
    args = parser.parse_args()
    if args.audited and args.action!='upgrade':
        parser.error('audited package validation is only available for upgrade')
    if os.geteuid() != 0:
        raise CoreError('root required')
    if args.action in ('refresh-health', 'rollback-health'):
        result = refresh_health(Path(args.directory).parent, args.bind, args.action == 'rollback-health')
        print('健康代理事务结果：'+result)
        return
    service = BackendService(args.binary, args.config, args.work, args.token_file, migrate_access=True)
    if args.action in ('refresh-connect','rollback-connect'):
        print('连接策略事务结果：'+refresh_connect_policy(Path(args.directory).parent,service,args.action=='rollback-connect'))
        return
    if args.action == 'upgrade':
        result = upgrade(args.binary, args.directory, args.archive, args.archive_sha,
                         args.binary_sha, args.version, args.protect, service, audited=args.audited)
    else:
        result = rollback(args.binary, args.directory, args.protect, service)
    print('核心事务结果：'+result)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('核心事务失败：'+(str(error) if isinstance(error, (CoreError, InstallError)) else type(error).__name__), file=sys.stderr)
        raise SystemExit(1)
