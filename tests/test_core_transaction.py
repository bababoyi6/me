"""Real private-file activation/recovery; no host services or real credentials."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("core_transaction", Path(__file__).resolve().parents[1] / "tools/core_transaction.py")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


class Service:
    def __init__(self, binary):
        self.binary = binary
        self.calls = []
        self.fail_ready = None
        self.fail_start = None

    def stop(self):
        self.calls.append("stop")

    def start(self):
        self.calls.append("start")
        if self.fail_start == core.digest(self.binary):
            raise RuntimeError("synthetic start failure")

    def ready(self):
        self.calls.append("ready")
        return core.digest(self.binary) != self.fail_ready

    def version(self, path):
        return "old" if Path(path).read_bytes() == b"SYNTHETIC_OLD_CORE" else "new"


class CoreTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tdh-core-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.binary = self.root / "telemt"
        self.binary.write_bytes(b"SYNTHETIC_OLD_CORE")
        self.binary.chmod(0o755)
        self.candidate = self.root / "candidate"
        self.candidate.write_bytes(b"SYNTHETIC_NEW_CORE")
        self.candidate.chmod(0o755)
        self.directory = self.root / "transaction"
        self.directory.mkdir(mode=0o700)
        self.old, self.new = core.digest(self.binary), core.digest(self.candidate)
        self.service = Service(self.binary)
        self.tx = core.CoreTransaction(self.binary, self.directory, self.service)
        self.protected = {}
        for name in ("state", "secret", "wg-key", "links", "config"):
            p = self.root / name
            p.write_bytes(b"SYNTHETIC_PROTECTED_" + name.encode())
            self.protected[p] = p.read_bytes()
        self.addCleanup(self.check_protected)

    def check_protected(self):
        for path, value in self.protected.items():
            self.assertEqual(path.read_bytes(), value)

    def activate(self):
        return self.tx.activate(self.candidate, self.new, "new", self.old)

    def test_commit_and_explicit_rollback_keep_configuration(self):
        self.assertEqual(self.activate(), "committed")
        self.assertEqual(core.digest(self.binary), self.new)
        record = self.tx.load()
        self.assertEqual(core.digest(self.directory / record["backup"]), self.old)
        self.assertEqual(self.tx.rollback(), "recovered")
        self.assertEqual(core.digest(self.binary), self.old)
        calls = self.service.calls[:]
        self.assertEqual(self.tx.rollback(), "unchanged")
        self.assertEqual(calls, self.service.calls)

    def test_same_content_does_not_replace_history_or_restart(self):
        self.activate()
        before = self.tx.journal.read_bytes()
        self.service.calls.clear()
        self.assertEqual(self.tx.activate(self.candidate, self.new, "new", self.new), "unchanged")
        self.assertEqual(self.service.calls, [])
        self.assertEqual(self.tx.journal.read_bytes(), before)

    def test_checksum_version_and_baseline_fail_before_service_change(self):
        for sha, version, current in (("0" * 64, "new", self.old),
                                      (self.new, "wrong", self.old), (self.new, "new", "0" * 64)):
            with self.subTest(sha=sha, version=version, baseline=current):
                with self.assertRaises(core.CoreError):
                    self.tx.activate(self.candidate, sha, version, current)
                self.assertEqual(core.digest(self.binary), self.old)
                self.assertEqual(self.service.calls, [])

    def test_readiness_failure_restores_previous_binary_and_service(self):
        self.service.fail_ready = self.new
        with self.assertRaisesRegex(core.CoreError, "did not become ready"):
            self.activate()
        self.assertEqual(core.digest(self.binary), self.old)
        self.assertEqual(self.tx.load()["phase"], "recovered")
        self.assertEqual(self.service.calls, ["stop", "start", "ready", "stop", "start", "ready"])

    def test_start_failure_restores_previous_binary(self):
        self.service.fail_start = self.new
        with self.assertRaisesRegex(RuntimeError, "start failure"):
            self.activate()
        self.assertEqual(core.digest(self.binary), self.old)
        self.assertEqual(self.tx.load()["phase"], "recovered")

    def test_atomic_rename_failure_restores_and_cleans_staging(self):
        original = core.os.replace
        failed = False
        def replace(source, target):
            nonlocal failed
            if Path(target) == self.binary and not failed:
                failed = True
                raise OSError("synthetic rename failure")
            return original(source, target)
        with mock.patch.object(core.os, "replace", side_effect=replace):
            with self.assertRaisesRegex(OSError, "rename failure"):
                self.activate()
        self.assertEqual(core.digest(self.binary), self.old)
        self.assertEqual(list(self.root.glob("*.stage-*")), [])

    def test_incomplete_backup_copy_cannot_replace_a_valid_previous_file(self):
        previous = self.directory / ("core.previous-" + self.old)
        previous.write_bytes(self.binary.read_bytes())
        with mock.patch.object(core.shutil, "copyfileobj", side_effect=lambda source, output, length: output.write(b"SYNTHETIC_SHORT_COPY")):
            with self.assertRaisesRegex(core.CoreError, "changed while staging"):
                self.activate()
        self.assertEqual(core.digest(previous), self.old)
        self.assertEqual(core.digest(self.binary), self.old)
        self.assertEqual(self.service.calls, [])

    def test_backup_and_prepared_journal_exist_before_stop(self):
        stop = self.service.stop
        def checked_stop():
            record = self.tx.load()
            self.assertEqual(record["phase"], "prepared")
            self.assertEqual(core.digest(self.directory / record["backup"]), self.old)
            stop()
        self.service.stop = checked_stop
        self.activate()

    def test_pending_transaction_recovers_after_process_loss(self):
        self.activate()
        record = self.tx.load()
        record["phase"] = "switching"
        self.tx.save(record)
        replacement = core.CoreTransaction(self.binary, self.directory, self.service)
        replacement.rollback()
        self.assertEqual(core.digest(self.binary), self.old)
        self.assertEqual(replacement.load()["phase"], "recovered")

    def test_failed_later_upgrade_preserves_successful_rollback_history(self):
        self.activate()
        previous = self.tx.journal.read_bytes()
        self.candidate.write_bytes(b"SYNTHETIC_THIRD_CORE")
        third = core.digest(self.candidate)
        self.service.fail_ready = third
        with self.assertRaises(core.CoreError):
            self.tx.activate(self.candidate, third, "new", self.new)
        self.assertEqual(core.digest(self.binary), self.new)
        recovered_history = self.tx.load()
        for key in ("old_sha256", "new_sha256", "backup", "phase"):
            self.assertEqual(recovered_history[key], json.loads(previous)[key])
        self.tx.rollback()
        self.assertEqual(core.digest(self.binary), self.old)

    def test_foreign_binary_change_is_not_overwritten_on_recovery(self):
        self.activate()
        self.binary.write_bytes(b"SYNTHETIC_EXTERNAL_ADMIN_CHANGE")
        before = self.service.calls[:]
        with self.assertRaisesRegex(core.CoreError, "outside this transaction"):
            self.tx.rollback()
        self.assertEqual(self.binary.read_bytes(), b"SYNTHETIC_EXTERNAL_ADMIN_CHANGE")
        self.assertEqual(self.service.calls, before)

    def test_corrupt_previous_binary_is_rejected_before_stopping(self):
        self.activate()
        (self.directory / self.tx.load()["backup"]).write_bytes(b"SYNTHETIC_CORRUPTION")
        before = self.service.calls[:]
        with self.assertRaisesRegex(core.CoreError, "previous core checksum"):
            self.tx.rollback()
        self.assertEqual(core.digest(self.binary), self.new)
        self.assertEqual(self.service.calls, before)

    def test_symlink_candidate_and_binary_are_rejected(self):
        self.candidate.unlink()
        self.candidate.symlink_to(self.binary)
        with self.assertRaises(core.CoreError):
            self.activate()
        self.candidate.unlink()
        self.candidate.write_bytes(b"SYNTHETIC_NEW_CORE")
        self.binary.unlink()
        self.binary.symlink_to(self.candidate)
        with self.assertRaises(core.CoreError):
            self.activate()
        self.assertEqual(self.service.calls, [])

    def test_symlink_lock_cannot_modify_unrelated_file(self):
        target = self.root / "unrelated"
        target.write_bytes(b"SYNTHETIC_UNRELATED")
        (self.directory / "lock").symlink_to(target)
        with self.assertRaises((OSError, core.CoreError)):
            self.activate()
        self.assertEqual(target.read_bytes(), b"SYNTHETIC_UNRELATED")

    def test_second_operator_is_rejected_without_service_calls(self):
        with self.tx.locked():
            with self.assertRaises(BlockingIOError):
                self.activate()
        self.assertEqual(self.service.calls, [])

    def test_journal_target_and_backup_traversal_are_rejected(self):
        self.activate()
        original = self.tx.load()
        for field, value in (("binary", str(self.root / "other")), ("backup", "../unrelated")):
            corrupt = dict(original)
            corrupt[field] = value
            self.tx.save(corrupt)
            with self.assertRaises(core.CoreError):
                self.tx.rollback()
        self.assertEqual(core.digest(self.binary), self.new)


if __name__ == "__main__":
    unittest.main()
