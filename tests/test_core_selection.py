"""Fresh-install channel pins and existing-core compatibility through the real Bash functions."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/'telemt-dual-hop.sh'
PINS={
    'official':('3.5.17','0570762ddcd79530d666b09e955b955bb52c9e134cc8b8ed1a0f50ad28d58a0e',
                'e8ff277bbb8ec4676d6264e593fe2a0d255facec8f1cb305e357a3859198ffd4'),
    'balanced2':('3.5.5+tdh.balanced2','b4fec3efd10288a9f211ad958337fa314a15486e72fd5c50ab870f22a19737e5',
                 '5dc5d9b5ac43a0f12a402159e127b3b69e18ccb355c99b0322e73d870d907100'),
    'audit2':('3.5.5+tdh.audit2','c205183e479716f015378e4e2241103ae8d488e06f6ef65946be0bd94624f356',
              '2e5f864461be842dbca2ee8638f3b68327f56d5f1666ea0f2622a491b2ba75d1'),
}


class CoreSelectionTests(unittest.TestCase):
    def test_previous_release_both_architectures_and_join_codes_remain_compatible(self):
        for version,allowed in (('3.5.17',True),('3.5.16',True),('3.5.15',False),('untrusted',False)):
            result=self.shell('join_core_version_is_compatible "$TEST_VERSION"',TEST_VERSION=version)
            self.assertEqual(result.returncode==0,allowed)
        with tempfile.TemporaryDirectory() as folder:
            binary=Path(folder)/'telemt';binary.write_text('fixture');binary.chmod(0o755)
            for arch,sha in (('x86_64','1954f18faeb9f5838e9444880f2ffb00704c6bd5b2836bc86dd64611537d49b8'),
                    ('aarch64','ebaec212fd5556ceca7fb0fcc4d3d23c01a8883d20a4cbc567219022d57c2a0b')):
                body='''uname() { echo "$TEST_ARCH"; }
timeout() { printf '%s\\n' "$TEST_VERSION"; }
sha256sum() { printf '%s  %s\\n' "$TEST_HASH" "$TDH_BIN"; }
telemt_binary_is_expected'''
                for version,accepted in (('telemt 3.5.16',True),('telemt 3.5.17',False)):
                    result=self.shell(body,TDH_BIN=str(binary),TEST_ARCH=arch,TEST_VERSION=version,TEST_HASH=sha)
                    self.assertEqual(result.returncode==0,accepted,(arch,version,result.stderr))

    def shell(self,body,**variables):
        env=dict(os.environ,TDH_SELECTION_SCRIPT=str(SCRIPT),**variables)
        return subprocess.run(['bash','-c','set -Eeuo pipefail\nTDH_SOURCE_ONLY=1 source "$TDH_SELECTION_SCRIPT"\n'+body],
                              env=env,capture_output=True,text=True,timeout=5)

    def test_default_and_explicit_fallback_bind_version_archive_binary_and_url(self):
        for channel in (None,'','official','balanced2','audit2'):
            if channel is None:
                result=self.shell('unset TDH_NEW_INSTALL_CORE; select_amd64_core')
            else:
                result=self.shell('select_amd64_core',TDH_NEW_INSTALL_CORE=channel)
            self.assertEqual(result.returncode,0,result.stderr)
            asset,version,archive,binary,url=result.stdout.strip().split('\t')
            expected=PINS[channel or 'balanced2']
            self.assertEqual((version,archive,binary),expected)
            if channel=='official':
                self.assertEqual(asset,'telemt-x86_64-linux-gnu.tar.gz')
                self.assertEqual(url,f'https://github.com/telemt/telemt/releases/download/{version}/{asset}')
                continue
            suffix=version.replace('+','-')
            self.assertEqual(asset,f'telemt-{suffix}-x86_64-linux-gnu.tar.gz')
            self.assertEqual(url,f'https://github.com/bababoyi6/me/releases/download/telemt-core-{suffix}/{asset}')

    def test_unknown_channel_rejected_before_download(self):
        with tempfile.TemporaryDirectory() as folder:
            result=self.shell('uname() { echo x86_64; }\ncurl() { touch "$TDH_BASE/download-attempted"; }\ndownload_telemt',
                              TDH_NEW_INSTALL_CORE='unknown',TDH_BASE=folder,TDH_BIN=folder+'/telemt')
            self.assertNotEqual(result.returncode,0)
            self.assertEqual(list(Path(folder).iterdir()),[])

    def test_new_manager_accepts_both_pinned_cores_and_rejects_version_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as folder:
            binary=Path(folder)/'telemt';binary.write_text('fixture');binary.chmod(0o755)
            body='''timeout() { printf '%s\\n' "$TEST_VERSION"; }
sha256sum() { printf '%s  %s\\n' "$TEST_HASH" "$TDH_BIN"; }
telemt_binary_is_expected'''
            for channel,(version,_,sha) in PINS.items():
                for actual_sha,accepted in ((sha,True),('0'*64,False)):
                    result=self.shell(body,TDH_BIN=str(binary),TEST_VERSION='telemt '+version,TEST_HASH=actual_sha)
                    self.assertEqual(result.returncode==0,accepted,(channel,result.stderr))

    def test_arm_never_receives_the_x86_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            result=self.shell('uname() { echo aarch64; }\ncurl() { touch "$TDH_BASE/download-attempted"; }\ndownload_telemt',
                              TDH_NEW_INSTALL_CORE='balanced2',TDH_BASE=folder,TDH_BIN=folder+'/telemt')
            self.assertNotEqual(result.returncode,0)
            self.assertEqual(list(Path(folder).iterdir()),[])

    def test_arm_default_download_stays_official_for_unset_and_empty_selection(self):
        for channel in (None,''):
            with self.subTest(channel=channel),tempfile.TemporaryDirectory() as folder:
                body='''uname() { echo aarch64; }
curl() { printf '%s\\n' "$@" > "$TDH_BASE/fetch"; return 1; }
'''+('unset TDH_NEW_INSTALL_CORE\n' if channel is None else 'export TDH_NEW_INSTALL_CORE=\n')+'download_telemt'
                result=self.shell(body,TDH_BASE=folder,TDH_BIN=folder+'/telemt')
                self.assertNotEqual(result.returncode,0)
                self.assertIn('https://github.com/telemt/telemt/releases/download/3.5.17/telemt-aarch64-linux-gnu.tar.gz',
                              (Path(folder)/'fetch').read_text().splitlines())
                self.assertEqual(sorted(p.name for p in Path(folder).iterdir()),['fetch'])

    def test_unknown_or_writable_existing_core_is_never_executed(self):
        with tempfile.TemporaryDirectory() as folder:
            binary=Path(folder)/'telemt';binary.write_text('fixture');binary.chmod(0o755)
            body='''timeout() { touch "$TDH_BASE/executed"; printf 'telemt 3.5.5\\n'; }
sha256sum() { printf '%s  %s\\n' "$TEST_HASH" "$TDH_BIN"; }
telemt_binary_is_expected'''
            result=self.shell(body,TDH_BASE=folder,TDH_BIN=str(binary),TEST_HASH='0'*64)
            self.assertNotEqual(result.returncode,0)
            self.assertFalse((Path(folder)/'executed').exists())
            binary.chmod(0o777)
            result=self.shell(body,TDH_BASE=folder,TDH_BIN=str(binary),TEST_HASH=PINS['official'][2])
            self.assertNotEqual(result.returncode,0)
            self.assertFalse((Path(folder)/'executed').exists())

    def test_baseline_switch_binds_audited_archive_and_version_to_existing_transaction(self):
        with tempfile.TemporaryDirectory() as folder:
            body='''load_state() { TDH_ROLE=backend1; WG_INTERFACE=tdh-b1; LOCAL_WG_IP=10.77.1.2; return 0; }
require_current_state() { :; }
telemt_binary_is_expected() { return 0; }
uname() { echo x86_64; }
curl() { printf '%s\\n' "$@" > "$TDH_BASE/fetch"; }
python3() { printf '%s\\n' "$@" > "$TDH_BASE/args"; cat >/dev/null; }
manage_core upgrade --maintenance balanced2
'''
            result=self.shell(body,TDH_BASE=folder,TDH_BIN=folder+'/telemt',TDH_CONFIG_DIR=folder+'/config',TDH_STATE=folder+'/state')
            self.assertEqual(result.returncode,0,result.stderr)
            args=(Path(folder)/'args').read_text().splitlines()
            self.assertEqual(args[1],'upgrade');self.assertIn('--audited',args)
            for key,expected in (('--version',PINS['balanced2'][0]),('--archive-sha',PINS['balanced2'][1]),('--binary-sha',PINS['balanced2'][2])):
                self.assertEqual(args[args.index(key)+1],expected)
            self.assertIn('https://github.com/bababoyi6/me/releases/download/telemt-core-3.5.5-tdh.balanced2/telemt-3.5.5-tdh.balanced2-x86_64-linux-gnu.tar.gz',
                (Path(folder)/'fetch').read_text().splitlines())

    def test_baseline_switch_rejects_arm_and_missing_maintenance_before_mutation(self):
        for arch,ack in (('aarch64','--maintenance'),('x86_64','')):
            with self.subTest(arch=arch,ack=ack),tempfile.TemporaryDirectory() as folder:
                result=self.shell('uname() { echo "$TEST_ARCH"; }; manage_core upgrade "$TEST_ACK" balanced2',
                    TDH_BASE=folder,TEST_ARCH=arch,TEST_ACK=ack)
                self.assertNotEqual(result.returncode,0)
                self.assertEqual(list(Path(folder).iterdir()),[])

    def test_both_legacy_official_architectures_require_the_exact_binary(self):
        with tempfile.TemporaryDirectory() as folder:
            binary=Path(folder)/'telemt';binary.write_text('fixture');binary.chmod(0o755)
            for arch,sha in (('x86_64','cd34e20e9f5c4457579479e321eba2e65496712f0e794df6acb5b63d2ee01dcd'),
                             ('aarch64','5f2031447df06a393af7e271a72c5553ea83bff9611d24c441b492236101f857')):
                result=self.shell('''uname() { echo "$TEST_ARCH"; }
timeout() { echo 'telemt 3.5.5'; }
sha256sum() { printf '%s  %s\\n' "$TEST_HASH" "$TDH_BIN"; }
telemt_binary_is_expected''', TDH_BIN=str(binary),TEST_ARCH=arch,TEST_HASH=sha)
                self.assertEqual(result.returncode,0,result.stderr)

    def test_core_panel_requires_explicit_maintenance_and_keeps_default_readonly(self):
        for reply,expected in (('',''),('\n',''),('1\nNO\n',''),('1\nMAINTENANCE\n','upgrade --maintenance balanced2'),
                               ('2\nMAINTENANCE\n','upgrade --maintenance'),('3\nMAINTENANCE\n','rollback --maintenance')):
            with self.subTest(reply=reply),tempfile.TemporaryDirectory() as folder:
                body='''load_state() { TDH_ROLE=backend1; return 0; }
require_current_state() { :; }
manage_core() { printf '%s' "$*" > "$TDH_BASE/called"; }
printf '%b' "$TEST_REPLY" | core_panel
'''
                binary=Path(folder)/'unknown-core'
                binary.write_text('#!/bin/sh\ntouch "$TDH_BASE/executed"\n');binary.chmod(0o755)
                result=self.shell(body,TDH_BASE=folder,TDH_BIN=str(binary),TEST_REPLY=reply)
                self.assertEqual(result.returncode,0,result.stderr)
                called=Path(folder)/'called'
                self.assertEqual(called.read_text() if called.exists() else '',expected)
                self.assertFalse((Path(folder)/'executed').exists())
