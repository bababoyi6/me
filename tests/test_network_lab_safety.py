"""Exercise namespace-lab refusal and cleanup without creating any network."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


@unittest.skipUnless(sys.platform == "linux", "network lab is Linux only")
class NetworkLabSafetyTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("network_lab", Path(__file__).with_name("network_lab.py"))
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.temp = tempfile.TemporaryDirectory(prefix="tdh-audit-safety-")
        self.addCleanup(self.temp.cleanup)
        self.args = SimpleNamespace(root=self.temp.name)

    def test_unprivileged_run_is_refused_before_any_command(self):
        with patch.object(self.module.os, "geteuid", return_value=1000), patch.object(self.module, "command") as cmd:
            with self.assertRaises(RuntimeError):
                self.module.Network(self.args)
            cmd.assert_not_called()

    def test_world_accessible_directory_is_refused(self):
        os.chmod(self.temp.name, 0o755)
        with patch.object(self.module.os, "geteuid", return_value=0), patch.object(self.module, "command") as cmd:
            with self.assertRaises(RuntimeError):
                self.module.Network(self.args)
            cmd.assert_not_called()

    def test_cleanup_only_removes_created_resources(self):
        with patch.object(self.module.os, "geteuid", return_value=0), patch.object(self.module, "command", return_value=""):
            lab = self.module.Network(self.args)
        own_key = Path(self.temp.name) / "wg-0.key"
        own_key.write_text("synthetic-own")
        other_key = Path(self.temp.name) / "wg-1.key"
        other_key.write_text("synthetic-existing-do-not-remove")
        lab.key_paths = [own_key]
        lab.created = [self.module.NAMES[0]]
        with patch.object(self.module.subprocess, "run") as run:
            lab.close()
        self.assertFalse(own_key.exists())
        self.assertEqual(other_key.read_text(), "synthetic-existing-do-not-remove")
        self.assertEqual(run.call_args.args[0], ["ip", "netns", "del", self.module.NAMES[0]])
        self.assertEqual(run.call_count, 1)
