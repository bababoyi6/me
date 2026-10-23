"""Reject pre-existing project paths before packages, prompts or transactions."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/'telemt-dual-hop.sh'


class FreshPathTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.base=self.root/'etc/telemt-dual-hop'
        self.work=self.root/'var/lib/telemt-dual-hop'
        self.binary=self.root/'usr/local/lib/telemt-dual-hop/telemt'
        self.manager=self.root/'usr/local/sbin/telemt-dual-hop'
        self.shortcut=self.root/'usr/local/bin/a'
        self.env=dict(os.environ,TDH_TEST_SCRIPT=str(SCRIPT),TDH_BASE=str(self.base),
                      TDH_WORK_ROOT=str(self.work),TDH_BIN=str(self.binary),TDH_MANAGER=str(self.manager),
                      TDH_SHORTCUT=str(self.shortcut),TDH_TEST_MARKER=str(self.root/'mutation'))

    def invoke(self,command,success=False):
        source='''set -Eeuo pipefail
TDH_SOURCE_ONLY=1 source "$TDH_TEST_SCRIPT"
check_os() { printf 'unexpected OS/package phase\n' >"$TDH_TEST_MARKER"; exit 91; }
prompt_secret() { printf 'unexpected prompt\n' >"$TDH_TEST_MARKER"; exit 92; }
rollback_transaction() { printf 'must not delete pre-existing data\n' >"$TDH_TEST_MARKER"; exit 93; }
'''
        result=subprocess.run(['bash','-c',source+command],env=self.env,capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode==0,success,result.stdout+result.stderr)
        self.assertFalse((self.root/'mutation').exists())

    def test_clean_preflight_creates_nothing(self):
        self.invoke('assert_fresh_install_paths',True)
        self.assertEqual(list(self.root.iterdir()),[])

    def test_existing_directory_and_customer_data_preserved_for_all_roles(self):
        self.base.mkdir(parents=True)
        customer=self.base/'customer.dat';customer.write_bytes(b'SYNTHETIC_MUST_REMAIN')
        for command in ('init_entry','install_backend backend1','install_backend backend2'):
            with self.subTest(command=command):
                self.invoke(command)
                self.assertEqual(customer.read_bytes(),b'SYNTHETIC_MUST_REMAIN')

    def test_foreign_core_manager_and_broken_symlink_are_preserved(self):
        for path in (self.binary,self.manager,self.shortcut):
            path.parent.mkdir(parents=True,exist_ok=True)
            if path==self.shortcut:
                path.symlink_to(self.root/'missing')
            else:
                path.write_bytes(b'UNRELATED_EXISTING_CONTENT')
            self.invoke('install_backend backend1')
            if path==self.shortcut:
                self.assertTrue(path.is_symlink())
            else:
                self.assertEqual(path.read_bytes(),b'UNRELATED_EXISTING_CONTENT')
            path.unlink()
            if path==self.binary:
                path.parent.rmdir()

    def test_existing_unit_wireguard_and_tuning_files_preserved(self):
        for suffix in ('systemd/system/telemt-dual-hop-telemt.service','wireguard/tdh2.conf',
                       'sysctl.d/90-telemt-dual-hop.conf','modules-load.d/90-telemt-dual-hop.conf'):
            path=self.root/'etc'/suffix;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(b'UNRELATED_CONFIGURATION')
            self.invoke('init_entry')
            self.assertEqual(path.read_bytes(),b'UNRELATED_CONFIGURATION')
            path.unlink()


if __name__=='__main__':unittest.main()
