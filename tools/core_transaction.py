"""Durable local core replacement engine; deliberately not a public upgrade CLI.

The caller must hold an entry-side maintenance permit and drain this backend.
This module never downloads a release, edits configuration or coordinates peers.
Service hooks are explicit so the exact transaction can be tested without systemd.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile


class CoreError(RuntimeError):
    pass


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def safe_directory(path):
    path = Path(path)
    info = path.lstat()
    if (not path.is_absolute() or path.resolve() != path or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid() or info.st_mode & 0o022):
        raise CoreError("unsafe transaction directory")


def safe_file(path):
    info = Path(path).lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_nlink != 1 or info.st_mode & 0o022):
        raise CoreError("unsafe transaction file")


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_copy(source, target, mode, expected_sha256=None, owner=None):
    target = Path(target)
    safe_directory(target.parent)
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=target.name + ".stage-", dir=target.parent)
        with os.fdopen(fd, "wb") as output, Path(source).open("rb") as incoming:
            shutil.copyfileobj(incoming, output, 1024 * 1024)
            output.flush()
            if owner is not None:
                os.fchown(output.fileno(), *owner)
            os.fchmod(output.fileno(), mode)
            os.fsync(output.fileno())
        if expected_sha256 is not None and digest(temporary) != expected_sha256:
            raise CoreError("source changed while staging core")
        os.replace(temporary, target)
        temporary = None
        sync_directory(target.parent)
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


class CoreTransaction:
    """One backend only. Hooks implement stop/start, bounded ready and version."""

    def __init__(self, binary, directory, service, *, file_mode=0o755, file_owner=None):
        self.binary = Path(binary)
        self.directory = Path(directory)
        self.service = service
        if file_mode not in (0o640, 0o755) or (file_mode == 0o640 and file_owner is None):
            raise CoreError('explicit config ownership required')
        self.file_mode, self.file_owner = file_mode, file_owner
        self.target_metadata = ({'mode': file_mode, 'uid': file_owner[0], 'gid': file_owner[1]}
                                if file_owner is not None else None)
        safe_directory(self.binary.parent)
        safe_directory(self.directory)
        if self.directory.stat().st_mode & 0o077:
            raise CoreError("transaction storage must be private")
        self.journal = self.directory / "transaction.json"

    @contextmanager
    def locked(self):
        lock = self.directory / "lock"
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            safe_file(lock)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)

    def save(self, value):
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(prefix="journal-", dir=self.directory)
            with os.fdopen(fd, "w", encoding="ascii") as output:
                json.dump(value, output, sort_keys=True)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.journal)
            temporary = None
            sync_directory(self.directory)
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    def load(self):
        safe_file(self.journal)
        if self.journal.stat().st_size > 4096:
            raise CoreError("oversized transaction journal")
        value = json.loads(self.journal.read_text(encoding="ascii"))
        self.validate(value)
        return value

    def validate(self, value, allow_prior=True):
        if not isinstance(value, dict) or value.get("schema") != 1 or value.get("binary") != str(self.binary):
            raise CoreError("transaction journal belongs to another target")
        if value.get('target_metadata') != self.target_metadata:
            raise CoreError('transaction target permissions changed')
        for key in ("old_sha256", "new_sha256"):
            text = value.get(key, "")
            if len(text) != 64 or any(c not in "0123456789abcdef" for c in text):
                raise CoreError("invalid journal digest")
        if value.get("phase") not in ("prepared", "switching", "committed", "recovered"):
            raise CoreError("invalid journal phase")
        if value.get("backup") != "core.previous-" + value["old_sha256"]:
            raise CoreError("invalid backup identity")
        snapshot = value.get("snapshot")
        if snapshot is not None and (not isinstance(snapshot, str) or len(snapshot) != 17
                                     or not snapshot.startswith('snapshot-')
                                     or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789_' for c in snapshot[9:])):
            raise CoreError("invalid protected snapshot identity")
        if value.get("prior") is not None:
            if not allow_prior:
                raise CoreError("nested transaction history")
            self.validate(value["prior"], False)
            if value["prior"]["phase"] != "committed" or value["prior"]["new_sha256"] != value["old_sha256"]:
                raise CoreError("previous journal does not match this baseline")

    def restore(self, value):
        backup = self.directory / value["backup"]
        safe_file(backup)
        safe_file(self.binary)
        self.validate_target_metadata()
        if digest(backup) != value["old_sha256"]:
            raise CoreError("previous core checksum mismatch")
        if digest(self.binary) not in (value["old_sha256"], value["new_sha256"]):
            raise CoreError("core changed outside this transaction; refusing overwrite")
        self.service.stop()
        atomic_copy(backup, self.binary, self.file_mode, value["old_sha256"], self.file_owner)
        self.service.start()
        if not self.service.ready():
            raise CoreError("previous core did not regain readiness")
        value["phase"] = "recovered"
        self.save(value)

    def activate(self, candidate, expected_sha256, expected_version, current_sha256, snapshot=None):
        candidate = Path(candidate)
        with self.locked():
            safe_directory(candidate.parent)
            safe_file(self.binary)
            self.validate_target_metadata()
            safe_file(candidate)
            if candidate.stat().st_size > 64 * 1024**2:
                raise CoreError("candidate exceeds size limit")
            if digest(candidate) != expected_sha256:
                raise CoreError("candidate checksum mismatch")
            if self.service.version(candidate) != expected_version:
                raise CoreError("candidate version mismatch")
            if digest(self.binary) != current_sha256:
                raise CoreError("running core differs from the approved baseline")
            if self.journal.is_symlink():
                raise CoreError("unsafe transaction journal")
            prior = None
            if self.journal.exists():
                previous = self.load()
                if previous["phase"] not in ("committed", "recovered"):
                    raise CoreError("unfinished transaction; recover it before upgrading")
                if previous["phase"] == "committed" and previous["new_sha256"] == current_sha256:
                    prior = {k: previous[k] for k in ("schema", "binary", "old_sha256", "new_sha256", "backup", "phase")}
                    if 'snapshot' in previous:
                        prior['snapshot'] = previous['snapshot']
                    if 'target_metadata' in previous:
                        prior['target_metadata'] = previous['target_metadata']
            if expected_sha256 == current_sha256:
                return "unchanged"
            backup = self.directory / ("core.previous-" + current_sha256)
            if backup.exists() or backup.is_symlink():
                safe_file(backup)
            atomic_copy(self.binary, backup, 0o600, current_sha256)
            if digest(backup) != current_sha256:
                raise CoreError("baseline changed while saving backup")
            value = {"schema": 1, "binary": str(self.binary), "old_sha256": current_sha256,
                     "new_sha256": expected_sha256, "backup": backup.name, "phase": "prepared", "prior": prior}
            if snapshot is not None:
                value['snapshot'] = snapshot
            if self.target_metadata is not None:
                value['target_metadata'] = self.target_metadata
            self.validate(value)
            self.save(value)
            try:
                self.service.stop()
                value["phase"] = "switching"
                self.save(value)
                atomic_copy(candidate, self.binary, self.file_mode, expected_sha256, self.file_owner)
                if digest(self.binary) != expected_sha256:
                    raise CoreError("activated core checksum mismatch")
                self.service.start()
                if not self.service.ready():
                    raise CoreError("candidate did not become ready")
                value["phase"] = "committed"
                self.save(value)
            except BaseException:
                self.restore(value)
                if prior is not None:
                    self.save(prior)
                raise
            return "committed"

    def validate_target_metadata(self):
        if self.target_metadata is not None:
            info = self.binary.stat()
            if (stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid) != (self.file_mode, *self.file_owner):
                raise CoreError('config permissions changed outside transaction')

    def rollback(self):
        with self.locked():
            value = self.load()
            if value["phase"] == "recovered" and digest(self.binary) == value["old_sha256"]:
                return "unchanged"
            pending = value["phase"] in ("prepared", "switching")
            self.restore(value)
            if pending and value.get("prior") is not None:
                self.save(value["prior"])
            return "recovered"


if __name__ == "__main__":
    raise SystemExit("Internal transaction library; no standalone production upgrade command.")
