"""Never source state through replaceable directories, symlinks or hard links."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.geteuid() == 0, 'state ownership requires root')
class StateSafetyTests(unittest.TestCase):
    def test_unsafe_state_is_refused_before_any_content_executes(self):
        source = Path(__file__).resolve().parents[1]/'telemt-dual-hop.sh'
        with tempfile.TemporaryDirectory(prefix='tdh-state-test-') as name:
            root = Path(name)
            parent = root/'state';parent.mkdir(mode=0o700)
            target = parent/'state.env'
            marker = root/'executed'
            content = f'TDH_ROLE=entry\ntouch "{marker}"\n'
            target.write_text(content);target.chmod(0o600)
            env = dict(os.environ, TDH_SOURCE_ONLY='1', TDH_STATE=str(target), TDH_TEST_SCRIPT=str(source))
            def invoke():
                return subprocess.run(['bash','-c','source "$TDH_TEST_SCRIPT"; load_state'],env=env,
                                      capture_output=True,text=True,timeout=5)
            # Validate the counterexample: safe local state really is sourced.
            self.assertEqual(invoke().returncode,0)
            self.assertTrue(marker.exists());marker.unlink()
            parent.chmod(0o777)
            self.assertNotEqual(invoke().returncode,0)
            self.assertFalse(marker.exists());parent.chmod(0o700)
            other = root/'other';os.link(target,other)
            self.assertNotEqual(invoke().returncode,0)
            self.assertFalse(marker.exists());other.unlink()
            target.rename(other);target.symlink_to(other)
            self.assertNotEqual(invoke().returncode,0)
            self.assertFalse(marker.exists())
