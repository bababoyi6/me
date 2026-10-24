"""Real filesystem checks for the opt-in private shared binary; never executes it."""
import hashlib
import os
from pathlib import Path
import tempfile
import unittest

from lab_files import link_private_binary


@unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'private root-owned lab staging')
class SharedLabBinaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='tdh-audit-inode-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'telemt-reference'
        self.source.write_bytes(b'non-executable synthetic content')
        self.source.chmod(0o755)
        self.target = self.root/'target'
        self.digest = hashlib.sha256(self.source.read_bytes()).hexdigest()

    def test_shared_inode_preserves_source_permissions_and_cleanup(self):
        before = self.source.stat()
        link_private_binary(self.source, self.target, self.root, self.digest)
        self.assertTrue(os.path.samefile(self.source, self.target))
        self.assertEqual(self.target.stat().st_nlink, 2)
        self.assertEqual(self.source.stat().st_mode, before.st_mode)
        self.target.unlink()
        self.assertEqual(self.source.stat().st_nlink, 1)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.digest)

    def test_unsafe_sources_and_roots_are_rejected(self):
        self.source.chmod(0o775)
        with self.assertRaises(ValueError):
            link_private_binary(self.source, self.target, self.root, self.digest)
        self.source.chmod(0o755)
        alias = self.root/'alias'
        alias.symlink_to(self.source)
        with self.assertRaises(ValueError):
            link_private_binary(alias, self.target, self.root, self.digest)
        self.root.chmod(0o755)
        with self.assertRaises(ValueError):
            link_private_binary(self.source, self.target, self.root, self.digest)
        self.root.chmod(0o700)
        other = self.root/'tdh-audit-other'
        other.mkdir(mode=0o700)
        with self.assertRaises(ValueError):
            link_private_binary(self.source, self.target, other, self.digest)
        self.assertFalse(self.target.exists())

    def test_bad_digest_unlinks_only_the_new_link(self):
        with self.assertRaises(ValueError):
            link_private_binary(self.source, self.target, self.root, '0'*64)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.source.stat().st_nlink, 1)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.digest)
