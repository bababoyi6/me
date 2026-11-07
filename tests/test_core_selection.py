"""Fresh-install channel pins and existing-core compatibility through the real Bash functions."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/'telemt-dual-hop.sh'
PINS={
    'official':('3.5.16','e6f46b2f3becf29bfd555cec8a059e429f7c082c1af402499b3a7940cf8ebd34',
                '1954f18faeb9f5838e9444880f2ffb00704c6bd5b2836bc86dd64611537d49b8'),
    'balanced2':('3.5.5+tdh.balanced2','b4fec3efd10288a9f211ad958337fa314a15486e72fd5c50ab870f22a19737e5',
                 '5dc5d9b5ac43a0f12a402159e127b3b69e18ccb355c99b0322e73d870d907100'),
    'audit2':('3.5.5+tdh.audit2','c205183e479716f015378e4e2241103ae8d488e06f6ef65946be0bd94624f356',
              '2e5f864461be842dbca2ee8638f3b68327f56d5f1666ea0f2622a491b2ba75d1'),
}


class CoreSelectionTests(unittest.TestCase):
    def shell(self,body,**variables):
        env=dict(os.environ,TDH_SELECTION_SCRIPT=str(SCRIPT),**variables)
        return subprocess.run(['bash','-c','set -Eeuo pipefail\nTDH_SOURCE_ONLY=1 source "$TDH_SELECTION_SCRIPT"\n'+body],
                              env=env,capture_output=True,text=True,timeout=5)

    def test_default_and_explicit_fallback_bind_version_archive_binary_and_url(self):
        for channel in ('','official','balanced2','audit2'):
            result=self.shell('select_amd64_core',TDH_NEW_INSTALL_CORE=channel)
            self.assertEqual(result.returncode,0,result.stderr)
            asset,version,archive,binary,url=result.stdout.strip().split('\t')
            expected=PINS[channel or 'official']
            self.assertEqual((version,archive,binary),expected)
            if channel in ('', 'official'):
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
