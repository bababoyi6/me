"""Root-only fuse checks: remove an orphan we own, refuse a changed identity."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid


@unittest.skipUnless(sys.platform=='linux' and os.geteuid()==0,'private namespace check requires Linux root')
class EnduranceCleanupTests(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('endurance_cleanup',Path(__file__).resolve().parents[1]/'tools/endurance_cleanup.py')
        self.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.module)
        existing=subprocess.check_output(['ip','netns','list'],text=True)
        if any(line.split()[0] in self.module.RESERVED_NAMES for line in existing.splitlines()):
            self.skipTest('another private experiment owns the reserved names')
        self.parent=tempfile.TemporaryDirectory();self.addCleanup(self.parent.cleanup)
        self.root=Path(self.parent.name)/('tdh-audit-endurance-'+uuid.uuid4().hex[:12]);self.root.mkdir(mode=0o700)
        self.name='tdh-audit-a'
        subprocess.run(['ip','netns','add',self.name],check=True,timeout=5)
        self.identity=Path('/run/netns',self.name).stat()
        self.addCleanup(self.remove_fixture)

    def remove_fixture(self):
        path=Path('/run/netns',self.name)
        if path.exists():
            now=path.stat()
            if (now.st_dev,now.st_ino)==(self.identity.st_dev,self.identity.st_ino):
                subprocess.run(['ip','netns','del',self.name],check=True,timeout=5)

    def ledger(self,changed=False):
        value={self.name:{'device':self.identity.st_dev,'inode':self.identity.st_ino+int(changed)}}
        (self.root/'namespace-owner.json').write_text(json.dumps(value))

    def test_owned_orphan_is_removed_and_unrelated_file_is_kept(self):
        self.ledger();marker=self.root/'keep.dat';marker.write_bytes(b'KEEP')
        self.module.cleanup(self.root)
        self.assertFalse(Path('/run/netns',self.name).exists())
        self.assertEqual(marker.read_bytes(),b'KEEP')
        self.assertTrue(json.loads((self.root/'fuse-cleanup.json').read_text())['complete'])

    def test_namespace_identity_mismatch_is_preserved_and_fails_acceptance(self):
        self.ledger(changed=True)
        with self.assertRaises(AssertionError):self.module.cleanup(self.root)
        self.assertTrue(Path('/run/netns',self.name).exists())
        self.assertFalse(json.loads((self.root/'fuse-cleanup.json').read_text())['complete'])
