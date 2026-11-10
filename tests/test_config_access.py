"""Actual unprivileged directory opens, crash resume and credential protection."""
import json
import hashlib
import io
import os
from pathlib import Path
import subprocess
import signal
import sys
import tempfile
import tarfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from core_upgrade import ConfigAccessMigration, upgrade, rollback
from core_transaction import CoreError


@unittest.skipUnless(hasattr(os, 'geteuid') and os.geteuid() == 0, 'needs isolated root test')
class ConfigAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='tdh-access-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o755)
        self.base = self.root/'base'; self.base.mkdir(mode=0o710)
        os.chown(self.base, 0, 65534)
        self.store = self.base/'core-upgrade'; self.store.mkdir(mode=0o700)
        self.binary = self.root/'binary'; self.binary.write_bytes(b'LEGACY')
        self.secret = self.base/'state.env'; self.secret.write_bytes(b'SYNTHETIC_SECRET')
        self.secret.chmod(0o600)
        self.access = ConfigAccessMigration(self.base, self.store, self.binary, 65534)

    def opened(self, path, flags):
        return subprocess.run([sys.executable,'-c', 'import os,sys; os.close(os.open(sys.argv[1],int(sys.argv[2])))',
                               str(path),str(flags)], user=65534,group=65534,extra_groups=[],cwd='/',
                              capture_output=True,timeout=5).returncode == 0

    def test_old_search_only_mode_rejects_upstream_style_directory_open(self):
        self.assertFalse(self.opened(self.base, os.O_RDONLY | os.O_DIRECTORY))
        self.assertTrue(self.access.prepare())
        self.assertTrue(self.opened(self.base, os.O_RDONLY | os.O_DIRECTORY))
        self.assertFalse(self.opened(self.secret, os.O_RDONLY))
        self.assertEqual(self.secret.read_bytes(), b'SYNTHETIC_SECRET')
        self.assertEqual(self.access.record.stat().st_mode & 0o777, 0o600)

    def test_repeated_upgrade_and_rollback_resume_from_same_immutable_record(self):
        self.assertTrue(self.access.prepare())
        original = self.access.record.read_bytes()
        self.assertFalse(self.access.prepare())
        self.access.restore(); self.access.restore()
        self.assertEqual(self.base.stat().st_mode & 0o777, 0o710)
        self.assertTrue(self.access.prepare())
        self.assertEqual(original, self.access.record.read_bytes())
        # Simulate a crash after the record was saved but before chmod.
        self.base.chmod(0o710)
        self.assertTrue(self.access.prepare())

    def test_record_publication_sigkill_remains_single_link_and_resumable(self):
        code='''import os,signal,sys
sys.path.insert(0,sys.argv[1])
import core_upgrade as m
actual=m.publish_new
def stop_after_publication(source,target):
    actual(source,target)
    os.kill(os.getpid(),signal.SIGKILL)
m.publish_new=stop_after_publication
m.ConfigAccessMigration(sys.argv[2],sys.argv[3],sys.argv[4],65534).prepare()
'''
        result=subprocess.run([sys.executable,'-c',code,str(Path(__file__).resolve().parents[1]/'tools'),
            str(self.base),str(self.store),str(self.binary)],capture_output=True,timeout=5)
        self.assertEqual(result.returncode,-signal.SIGKILL)
        self.assertEqual(self.base.stat().st_mode & 0o777,0o710)
        self.assertEqual(self.access.record.stat().st_nlink,1)
        self.assertTrue(self.access.prepare())
        self.assertEqual(self.base.stat().st_mode & 0o777,0o750)
        self.assertEqual(self.secret.read_bytes(),b'SYNTHETIC_SECRET')

    def test_newer_core_never_receives_legacy_directory_permissions(self):
        self.access.prepare()
        self.binary.write_bytes(b'NEW_CORE')
        self.access.restore()
        self.assertEqual(self.base.stat().st_mode & 0o777, 0o750)
        self.base.chmod(0o710)
        with self.assertRaises(CoreError): self.access.prepare()

    def test_custom_permissions_group_and_symlink_are_refused(self):
        self.store.chmod(0o750)
        with self.assertRaises(CoreError): self.access.prepare()
        self.store.chmod(0o700)
        self.base.chmod(0o770)
        with self.assertRaises(CoreError): self.access.prepare()
        self.base.chmod(0o710)
        os.chown(self.base, 0, 0)
        with self.assertRaises(CoreError): self.access.prepare()
        os.chown(self.base, 0, 65534)
        other = self.root/'other';other.mkdir()
        self.base.rename(self.root/'saved');self.base.symlink_to(other)
        with self.assertRaises(CoreError): self.access.prepare()

    def test_modified_record_cannot_redirect_permission_changes(self):
        self.access.prepare()
        data = json.loads(self.access.record.read_text())
        data['path'] = str(self.root)
        self.access.record.write_text(json.dumps(data))
        with self.assertRaises(CoreError): self.access.restore()

    def transaction(self, reject=False):
        config_dir=self.base/'telemt';config_dir.mkdir(mode=0o750)
        config=config_dir/'telemt.toml';config.write_bytes(b'UNCHANGED_CONFIG')
        payload=b'CANDIDATE'
        archive=self.root/'candidate.tar.gz'
        with tarfile.open(archive,'w:gz') as output:
            item=tarfile.TarInfo('telemt');item.size=len(payload)
            output.addfile(item,io.BytesIO(payload))
        class Service:
            migrate_access=True
            identity={'group':65534}
            def version(self,path):return 'telemt 3.5.16'
            def validate_candidate(inner,path):
                self.assertEqual(self.base.stat().st_mode & 0o777,0o750)
                if reject:raise CoreError('rejected preflight')
            def stop(self):pass
            def start(self):pass
            def ready(self):return True
        service=Service();service.config=config
        return service,config,archive,hashlib.sha256(payload).hexdigest()

    def test_real_transaction_migrates_before_preflight_and_restores_after_rollback(self):
        service,config,archive,new_sha=self.transaction()
        archive_sha=hashlib.sha256(archive.read_bytes()).hexdigest()
        self.assertEqual(upgrade(self.binary,self.store,archive,archive_sha,new_sha,'3.5.16',[config],service),'committed')
        self.assertEqual(self.base.stat().st_mode & 0o777,0o750)
        self.assertEqual(rollback(self.binary,self.store,[config],service),'recovered')
        self.assertEqual(self.base.stat().st_mode & 0o777,0o710)
        self.assertEqual(config.read_bytes(),b'UNCHANGED_CONFIG')

    def test_preflight_failure_restores_permissions_without_spending_snapshot_budget(self):
        service,config,archive,new_sha=self.transaction(reject=True)
        with self.assertRaisesRegex(CoreError,'rejected preflight'):
            upgrade(self.binary,self.store,archive,hashlib.sha256(archive.read_bytes()).hexdigest(),new_sha,'3.5.16',[config],service)
        self.assertEqual(self.base.stat().st_mode & 0o777,0o710)
        self.assertEqual(list(self.store.glob('snapshot-*')),[])
        self.assertEqual(self.binary.read_bytes(),b'LEGACY')
        backup=self.store/('core.previous-'+hashlib.sha256(b'LEGACY').hexdigest())
        self.assertEqual(backup.read_bytes(),b'LEGACY')
        self.assertEqual(backup.stat().st_mode & 0o777,0o600)

    def test_interrupted_access_only_recovery_refuses_changed_core(self):
        service,config,_,_=self.transaction()
        self.access.prepare()
        self.assertEqual(rollback(self.binary,self.store,[config],service),'access-restored')
        self.access.prepare();self.binary.write_bytes(b'OTHER')
        with self.assertRaisesRegex(CoreError,'baseline changed'):
            rollback(self.binary,self.store,[config],service)
        self.assertEqual(self.base.stat().st_mode & 0o777,0o750)

    def test_state_saves_preserve_legacy_mode_until_explicit_migration(self):
        script=Path(__file__).resolve().parents[1]/'telemt-dual-hop.sh'
        env=dict(os.environ, TDH_BASE=str(self.base), TDH_TEST_SCRIPT=str(script))
        code='''set -Eeuo pipefail
TDH_SOURCE_ONLY=1 source "$TDH_TEST_SCRIPT"
TDH_ROLE=backend1
getent() { [[ $* == 'group telemt' ]]; }
chown() { if [[ $1 == root:telemt ]]; then shift; command chown 0:65534 "$@"; else command chown "$@"; fi; }
save_state
'''
        subprocess.run(['bash','-c',code],env=env,check=True,capture_output=True,timeout=5)
        self.assertEqual(self.base.stat().st_mode & 0o777,0o710)
        self.assertEqual(self.secret.stat().st_mode & 0o777,0o600)
        # A fresh backend gets the required group-readable parent.
        self.base.chmod(0o700)
        subprocess.run(['bash','-c',code],env=env,check=True,capture_output=True,timeout=5)
        self.assertEqual(self.base.stat().st_mode & 0o777,0o750)


if __name__ == '__main__': unittest.main()
