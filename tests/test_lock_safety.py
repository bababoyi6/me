"""Manager locks preserve files, refuse unsafe objects and coordinate old/new code."""
import os
from pathlib import Path
import select
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LockSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tdh-lock-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.lock = self.base / "manager.lock"
        self.env = dict(os.environ, TDH_LOCK=str(self.lock), TDH_TEST_SCRIPT=str(ROOT / "telemt-dual-hop.sh"),
                        TDH_LEGACY_LOCK=str(ROOT / "tests/fixtures/legacy-acquire-lock.sh"))
        self.source = 'set -Eeuo pipefail\nTDH_SOURCE_ONLY=1 source "$TDH_TEST_SCRIPT"\n'

    def invoke(self, extra="", ok=True):
        result = subprocess.run(["bash", "-c", self.source + extra + "\nacquire_lock"], env=self.env,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return result

    def test_legacy_counterexample_truncates_symlink_target(self):
        target = self.base / "synthetic-target"
        target.write_bytes(b"SYNTHETIC_DO_NOT_TRUNCATE")
        self.lock.symlink_to(target)
        self.invoke('source "$TDH_LEGACY_LOCK"')
        self.assertEqual(target.read_bytes(), b"")

    def test_rejects_symlink_without_touching_target(self):
        target = self.base / "synthetic-target"
        expected = b"SYNTHETIC_DO_NOT_TRUNCATE"
        target.write_bytes(expected)
        self.lock.symlink_to(target)
        self.invoke(ok=False)
        self.assertEqual(target.read_bytes(), expected)

    def test_rejects_fifo_and_hardlinked_file_without_blocking(self):
        os.mkfifo(self.lock)
        self.invoke(ok=False)
        self.lock.unlink()
        target = self.base / "synthetic-target"
        target.write_bytes(b"SYNTHETIC_CONTENT")
        os.link(target, self.lock)
        self.invoke(ok=False)
        self.assertEqual(target.read_bytes(), b"SYNTHETIC_CONTENT")

    def test_preserves_legacy_inode_and_contents(self):
        self.lock.write_bytes(b"SYNTHETIC_EXISTING_LOCK_CONTENTS")
        self.lock.chmod(0o644)
        before = self.lock.stat()
        self.invoke()
        self.assertEqual(self.lock.stat().st_ino, before.st_ino)
        self.assertEqual(self.lock.read_bytes(), b"SYNTHETIC_EXISTING_LOCK_CONTENTS")

    def test_creates_private_regular_lock_and_no_temporary_residue(self):
        self.invoke()
        self.assertEqual(self.lock.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.lock.stat().st_nlink, 1)
        self.assertEqual(list(self.base.iterdir()), [self.lock])

    def test_rejects_writable_file_or_unsafe_shared_directory(self):
        self.lock.write_bytes(b"SYNTHETIC_EXISTING")
        self.lock.chmod(0o666)
        self.invoke(ok=False)
        self.assertEqual(self.lock.read_bytes(), b"SYNTHETIC_EXISTING")
        self.lock.chmod(0o600)
        self.base.chmod(0o777)
        self.invoke(ok=False)
        self.base.chmod(0o1777)
        self.invoke()

    def test_legacy_holder_blocks_new_manager_until_release(self):
        held = subprocess.Popen(["bash", "-c", self.source + '''source "$TDH_LEGACY_LOCK"
acquire_lock
printf ready
read -r _
'''], env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertTrue(select.select([held.stdout], [], [], 3)[0], "legacy lock holder did not become ready")
            self.assertEqual(held.stdout.read(5), b"ready")
            before = self.lock.stat().st_ino
            result = self.invoke(ok=False)
            self.assertIn("另一个", result.stderr)
            self.assertEqual(self.lock.stat().st_ino, before)
        finally:
            held.communicate(b"release\n", timeout=5)
        self.invoke()
