"""Fresh-install channel pins and existing-core compatibility through the real Bash functions."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/'telemt-dual-hop.sh'
PINS={
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
        for channel in ('','balanced2','audit2'):
            result=self.shell('select_amd64_core',TDH_NEW_INSTALL_CORE=channel)
            self.assertEqual(result.returncode,0,result.stderr)
            asset,version,archive,binary,url=result.stdout.strip().split('\t')
            expected=PINS[channel or 'balanced2']
            self.assertEqual((version,archive,binary),expected)
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
