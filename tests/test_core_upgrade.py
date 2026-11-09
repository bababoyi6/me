"""Real archive/backup/transaction bytes; service failures are injected explicitly."""
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import core_upgrade as upgrade
from core_transaction import digest, CoreError


class Service:
    def __init__(self):
        self.calls = []
        self.valid = True
        self.starts = 0
        self.fail_new = False
    def version(self, binary): return 'telemt 3.5.16'
    def validate_candidate(self, binary):
        if not self.valid: raise CoreError('candidate rejects old config')
    def stop(self): self.calls.append('stop')
    def start(self):
        self.starts += 1
        self.calls.append('start')
    def ready(self): return not (self.fail_new and self.starts == 1)


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.binary = self.root/'telemt'
        self.binary.write_bytes(b'OLD_CORE')
        self.binary.chmod(0o755)
        self.store = self.root/'transaction'
        self.store.mkdir(mode=0o700)
        self.config = self.root/'old.toml'
        self.config.write_bytes(b'SYNTHETIC_SECRET_UNCHANGED')
        self.config.chmod(0o640)
        self.archive = self.root/'core.tar.gz'
        self.payload = b'NEW_PINNED_CORE'
        with tarfile.open(self.archive, 'w:gz') as archive:
            info = tarfile.TarInfo('telemt')
            info.size = len(self.payload)
            archive.addfile(info, io.BytesIO(self.payload))
        self.new_sha = hashlib.sha256(self.payload).hexdigest()
        self.service = Service()

    def activate(self):
        return upgrade.upgrade(self.binary, self.store, self.archive, digest(self.archive),
                               self.new_sha, '3.5.16', [self.config], self.service)

    def audited_fixture(self):
        data=bytearray(128);data[:6]=b'\x7fELF\x02\x01';data[18:20]=b'\x3e\x00'
        self.payload=bytes(data);self.new_sha=hashlib.sha256(self.payload).hexdigest()
        files={'telemt':self.payload,'TELEMT-LICENSE.txt':b'synthetic license','NOTICE.txt':b'synthetic notice',
            'source.patch':b'synthetic patch','build-provenance.json':b'{}'}
        manifest={'schema':1,'version':'3.5.16','architecture':'x86_64','os':'linux','libc':'gnu',
            'minimum_glibc':'2.35','unofficial':True,'files':{n:{'sha256':hashlib.sha256(b).hexdigest(),
            'bytes':len(b),'mode':'0755' if n=='telemt' else '0644'} for n,b in files.items()}}
        files['manifest.json']=json.dumps(manifest).encode()
        with tarfile.open(self.archive,'w:gz') as archive:
            for name,content in files.items():
                member=tarfile.TarInfo(name);member.size=len(content);archive.addfile(member,io.BytesIO(content))

    def test_audited_source_archive_is_private_and_survives_exact_rollback(self):
        self.audited_fixture();original=self.config.read_bytes();archive_bytes=self.archive.read_bytes()
        result=upgrade.upgrade(self.binary,self.store,self.archive,digest(self.archive),self.new_sha,'3.5.16',
            [self.config],self.service,audited=True)
        self.assertEqual(result,'committed')
        cached=self.store/('audited-core-'+self.new_sha+'.tar.gz')
        self.assertEqual(cached.read_bytes(),archive_bytes)
        self.assertEqual(cached.stat().st_mode & 0o777,0o600)
        self.assertEqual(upgrade.rollback(self.binary,self.store,[self.config],self.service),'recovered')
        self.assertEqual(self.binary.read_bytes(),b'OLD_CORE')
        self.assertEqual(self.config.read_bytes(),original)
        self.assertEqual(cached.read_bytes(),archive_bytes)

    def test_audited_cache_tamper_symlink_and_hardlink_are_not_replaced(self):
        self.audited_fixture();cached=self.store/('audited-core-'+self.new_sha+'.tar.gz')
        for kind in ('content','symlink','hardlink'):
            if kind=='content':cached.write_bytes(b'changed');cached.chmod(0o600)
            elif kind=='symlink':cached.symlink_to(self.archive)
            else:os.link(self.archive,cached)
            with self.subTest(kind=kind),self.assertRaises(CoreError):
                upgrade.preserve_audited_archive(self.store,self.archive,digest(self.archive),self.new_sha)
            cached.unlink()
        self.assertEqual(self.service.calls,[])

    def test_audited_cache_limit_is_enforced_before_stopping_service(self):
        self.audited_fixture()
        for n in range(8):
            cached=self.store/('audited-core-'+str(n).zfill(64)+'.tar.gz');cached.write_bytes(b'kept');cached.chmod(0o600)
        with self.assertRaisesRegex(CoreError,'audited archive retention limit'):
            upgrade.upgrade(self.binary,self.store,self.archive,digest(self.archive),self.new_sha,'3.5.16',
                [self.config],self.service,audited=True)
        self.assertEqual(self.service.calls,[])
        self.assertEqual(self.binary.read_bytes(),b'OLD_CORE')

    def test_old_config_and_credentials_are_backed_up_without_rewriting(self):
        before = self.config.read_bytes()
        self.assertEqual(self.activate(), 'committed')
        self.assertEqual(self.config.read_bytes(), before)
        snapshot = next(self.store.glob('snapshot-*'))
        self.assertEqual((snapshot/'0').read_bytes(), before)
        self.assertEqual((snapshot/'0').stat().st_mode & 0o777, 0o600)
        self.assertEqual(snapshot.stat().st_mode & 0o777, 0o700)
        self.assertNotIn(before.decode(), (snapshot/'manifest.json').read_text())
        self.assertEqual(upgrade.rollback(self.binary, self.store, [self.config], self.service), 'recovered')
        self.assertEqual(self.binary.read_bytes(), b'OLD_CORE')

    def test_repeat_upgrade_is_noop_and_keeps_rollback(self):
        self.activate()
        calls = self.service.calls[:]
        pointer = (self.store/'transaction.json').read_bytes()
        self.assertEqual(self.activate(), 'unchanged')
        self.assertEqual(self.service.calls, calls)
        self.assertEqual((self.store/'transaction.json').read_bytes(), pointer)

    def test_config_incompatibility_and_archive_tamper_do_not_stop_old_service(self):
        self.service.valid = False
        with self.assertRaises(CoreError): self.activate()
        self.assertEqual(self.service.calls, [])
        self.assertEqual(list(self.store.iterdir()), [])
        self.service.valid = True
        self.new_sha = '0'*64
        with self.assertRaises(Exception): self.activate()
        self.assertEqual(self.service.calls, [])
        self.assertEqual(self.binary.read_bytes(), b'OLD_CORE')

    def test_readiness_failure_auto_restores_old_binary(self):
        self.service.fail_new = True
        with self.assertRaisesRegex(CoreError, 'did not become ready'): self.activate()
        self.assertEqual(self.binary.read_bytes(), b'OLD_CORE')
        self.assertEqual(self.config.read_bytes(), b'SYNTHETIC_SECRET_UNCHANGED')

    def test_rollback_refuses_changed_config_and_tampered_snapshot(self):
        self.activate()
        self.config.write_bytes(b'CUSTOM_CHANGED_CONFIG')
        calls = self.service.calls[:]
        with self.assertRaisesRegex(CoreError, 'configuration changed'):
            upgrade.rollback(self.binary, self.store, [self.config], self.service)
        self.assertEqual(self.service.calls, calls)
        self.config.write_bytes(b'SYNTHETIC_SECRET_UNCHANGED')
        (next(self.store.glob('snapshot-*'))/'0').write_bytes(b'CORRUPTED')
        with self.assertRaisesRegex(CoreError, 'backup checksum'):
            upgrade.rollback(self.binary, self.store, [self.config], self.service)
        self.assertEqual(self.service.calls, calls)

    def test_symlinks_and_public_transaction_storage_are_rejected(self):
        target = self.root/'link'
        target.symlink_to(self.config)
        with self.assertRaises(CoreError): upgrade.save_snapshot(self.store, [target])
        self.store.chmod(0o777)
        with self.assertRaises(CoreError): self.activate()

    def test_generated_single_file_contains_exact_reviewed_sources(self):
        import embed_core_upgrade
        script = (Path(__file__).resolve().parents[1]/'telemt-dual-hop.sh').read_text()
        embedded = script.split(embed_core_upgrade.BEGIN)[1].split(embed_core_upgrade.END)[0]
        self.assertEqual(embedded, embed_core_upgrade.payload())

    def test_crash_recovery_uses_the_snapshot_bound_to_the_journal(self):
        self.activate()
        journal = self.store/'transaction.json'
        record = json.loads(journal.read_text())
        record['phase'] = 'switching'
        journal.write_text(json.dumps(record))
        # A stray/uncommitted snapshot or stale external pointer cannot redirect recovery.
        (self.store/'snapshot.json').write_text('{"folder":"snapshot-aaaaaaaa"}')
        self.assertEqual(upgrade.rollback(self.binary, self.store, [self.config], self.service), 'recovered')
        self.assertEqual(self.binary.read_bytes(), b'OLD_CORE')

    def test_failed_second_upgrade_preserves_prior_snapshot_identity(self):
        self.activate()
        prior = json.loads((self.store/'transaction.json').read_text())
        self.config.write_bytes(b'CONFIG_CHANGED_BETWEEN_UPGRADES')
        self.payload = b'SECOND_CORE'
        with tarfile.open(self.archive, 'w:gz') as archive:
            info = tarfile.TarInfo('telemt'); info.size = len(self.payload)
            archive.addfile(info, io.BytesIO(self.payload))
        self.new_sha = hashlib.sha256(self.payload).hexdigest()
        self.service.fail_new = True
        self.service.starts = 0
        with self.assertRaises(CoreError): self.activate()
        current = json.loads((self.store/'transaction.json').read_text())
        self.assertEqual(current['snapshot'], prior['snapshot'])
        with self.assertRaisesRegex(CoreError, 'configuration changed'):
            upgrade.rollback(self.binary, self.store, [self.config], self.service)

    def test_health_refresh_is_atomic_idempotent_and_reversible(self):
        target = self.root/'health_agent.py'
        old = b'print("old synthetic agent")\n'
        target.write_bytes(old)
        target.chmod(0o755)
        class HealthService(upgrade.HealthService):
            def stop(self): pass
            def start(self): pass
            def ready(self): return True
        service = HealthService('127.0.0.1')
        call = lambda rollback=False: upgrade.refresh_health(self.root, '127.0.0.1', rollback,
                                                             target=target, service=service)
        self.assertEqual(call(), 'committed')
        self.assertEqual(target.read_bytes(), (Path(upgrade.__file__).with_name('health_agent.py')).read_bytes())
        self.assertEqual(call(), 'unchanged')
        self.assertEqual(call(True), 'recovered')
        self.assertEqual(target.read_bytes(), old)

    def test_retry_after_partial_activation_cannot_report_unchanged(self):
        self.activate()
        journal = self.store/'transaction.json'
        record = json.loads(journal.read_text());record['phase'] = 'switching'
        journal.write_text(json.dumps(record))
        with self.assertRaisesRegex(CoreError, 'unfinished transaction'):
            self.activate()
        self.assertEqual(json.loads(journal.read_text())['phase'], 'switching')

    def test_special_file_backup_roots_are_never_silently_omitted(self):
        fifo = self.root/'special'
        os.mkfifo(fifo, 0o600)
        with self.assertRaisesRegex(CoreError, 'regular file or directory'):
            upgrade.protected_files([fifo])
