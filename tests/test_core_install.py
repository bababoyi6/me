"""Fresh-install trust boundaries; synthetic artifacts never execute."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('core_install', ROOT/'tools/core_install.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
VERSION = '3.5.5+tdh.audit2'


class CoreInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root/'core.tar.gz'
        binary = bytearray(128)
        binary[:6], binary[18:20] = b'\x7fELF\x02\x01', b'\x3e\x00'
        self.files = {'telemt':bytes(binary),'NOTICE.txt':b'Unofficial core', 'TELEMT-LICENSE.txt':b'Synthetic license',
                      'source.patch':b'Synthetic patch', 'build-provenance.json':b'{}'}
        self.manifest = {'schema':1,'version':VERSION,'architecture':'x86_64','os':'linux','libc':'gnu',
                         'minimum_glibc':'2.35','unofficial':True,
                         'files':{name:{'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),
                                       'mode':'0755' if name == 'telemt' else '0644'} for name,data in self.files.items()}}

    def pack(self, extras=(), omit=(), manifest=None):
        files = dict(self.files)
        files['manifest.json'] = json.dumps(self.manifest if manifest is None else manifest).encode()
        with tarfile.open(self.archive,'w:gz') as archive:
            for name,data in files.items():
                if name in omit:
                    continue
                info = tarfile.TarInfo(name); info.size = len(data)
                archive.addfile(info,io.BytesIO(data))
            for info,data in extras:
                archive.addfile(info,io.BytesIO(data) if data is not None else None)
        return module.checksum(self.archive)

    def payload(self, checksum):
        return module.archive_payload(self.archive,checksum,VERSION,hashlib.sha256(self.files['telemt']).hexdigest(),True)

    def test_embedded_source_matches_tested_module(self):
        script = (ROOT/'telemt-dual-hop.sh').read_text()
        body = script.split('# BEGIN GENERATED CORE INSTALLER\n')[1].split('# END GENERATED CORE INSTALLER\n')[0]
        self.assertEqual(body,(ROOT/'tools/core_install.py').read_text().rstrip()+'\n')

    def test_valid_bundle_returns_binary_and_original_notices(self):
        binary, notices = self.payload(self.pack())
        self.assertEqual(binary,self.files['telemt'])
        self.assertEqual(set(notices),module.AUDIT_FILES-{'telemt'})
        self.assertEqual(notices['TELEMT-LICENSE.txt'],self.files['TELEMT-LICENSE.txt'])

    def test_untrusted_archive_checksum_stops_before_tar_parsing(self):
        self.pack()
        with patch.object(module.tarfile,'open',side_effect=AssertionError('must not parse')):
            with self.assertRaisesRegex(module.InstallError,'archive checksum'):
                self.payload('0'*64)

    def test_missing_duplicate_or_extra_members_rejected(self):
        info = tarfile.TarInfo('telemt'); info.size = 1
        for omit,extras in ((('NOTICE.txt',),()),((),((info,b'x'),)),((),((tarfile.TarInfo('unexpected'),b''),))):
            with self.subTest(omit=omit,extras=bool(extras)):
                with self.assertRaises(module.InstallError):
                    self.payload(self.pack(omit=omit,extras=extras))

    def test_traversal_symlink_and_hardlink_rejected(self):
        for name,kind in (('../escape',tarfile.REGTYPE),('/absolute',tarfile.REGTYPE),
                          ('alias',tarfile.SYMTYPE),('alias',tarfile.LNKTYPE)):
            info = tarfile.TarInfo(name); info.type = kind; info.linkname = 'telemt'
            info.size = 1 if kind == tarfile.REGTYPE else 0
            with self.subTest(name=name,kind=kind),self.assertRaises(module.InstallError):
                self.payload(self.pack(extras=((info,b'x' if info.size else None),)))
        self.assertFalse((self.root.parent/'escape').exists())

    def test_manifest_version_architecture_and_member_hash_are_bound(self):
        for key,value in (('version','3.5.14'),('architecture','aarch64'),('unofficial',False)):
            manifest = dict(self.manifest,**{key:value})
            with self.subTest(key=key),self.assertRaisesRegex(module.InstallError,'identity'):
                self.payload(self.pack(manifest=manifest))
        self.manifest['files']['telemt']['sha256'] = '0'*64
        with self.assertRaisesRegex(module.InstallError,'member mismatch'):
            self.payload(self.pack())

    def test_binary_hash_and_elf_architecture_are_bound(self):
        checksum = self.pack()
        with self.assertRaisesRegex(module.InstallError,'binary checksum'):
            module.archive_payload(self.archive,checksum,VERSION,'0'*64,True)
        wrong = bytearray(self.files['telemt']); wrong[18:20] = b'\xb7\x00'
        self.files['telemt'] = bytes(wrong)
        with self.assertRaisesRegex(module.InstallError,'x86_64 ELF'):
            self.payload(self.pack())

    def test_fresh_install_runs_as_telemt_and_retains_license(self):
        checksum = self.pack()
        target = self.root/'telemt'
        with patch.object(module.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=65534,pw_gid=65534)), \
             patch.object(module.subprocess,'run',return_value=SimpleNamespace(stdout=('telemt '+VERSION+'\n').encode())) as run:
            module.fresh_install(self.archive,target,checksum,VERSION,hashlib.sha256(self.files['telemt']).hexdigest(),True)
        self.assertEqual(run.call_args.kwargs['user'],65534)
        self.assertEqual(run.call_args.kwargs['extra_groups'],[])
        self.assertEqual(target.read_bytes(),self.files['telemt'])
        self.assertEqual((self.root/'core-source-notice/TELEMT-LICENSE.txt').read_bytes(),self.files['TELEMT-LICENSE.txt'])
        self.assertFalse(list(self.root.glob('.telemt-install-*')))

    def test_existing_core_and_symlinks_are_preserved(self):
        target = self.root/'telemt'; target.write_bytes(b'existing')
        with self.assertRaisesRegex(module.InstallError,'existing core'):
            module.fresh_install(self.archive,target,'0'*64,VERSION)
        self.assertEqual(target.read_bytes(),b'existing')
        alias = self.root/'alias'; alias.symlink_to(target)
        with self.assertRaisesRegex(module.InstallError,'existing core'):
            module.fresh_install(self.archive,alias,'0'*64,VERSION)

    def test_wrong_runtime_version_leaves_no_installed_core(self):
        checksum = self.pack(); target = self.root/'telemt'
        with patch.object(module.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=65534,pw_gid=65534)), \
             patch.object(module.subprocess,'run',return_value=SimpleNamespace(stdout=b'telemt unknown\n')):
            with self.assertRaisesRegex(module.InstallError,'runtime version'):
                module.fresh_install(self.archive,target,checksum,VERSION,hashlib.sha256(self.files['telemt']).hexdigest(),True)
        self.assertFalse(target.exists())
        self.assertFalse((self.root/'core-source-notice').exists())
        self.assertFalse(list(self.root.glob('.telemt-install-*')))

    def test_member_count_and_size_are_bounded(self):
        extras = []
        for n in range(33):
            info = tarfile.TarInfo('directory'+str(n)); info.type = tarfile.DIRTYPE
            extras.append((info,None))
        with self.assertRaises(module.InstallError):
            module.archive_payload(self.archive,self.pack(extras=extras),VERSION)


if __name__ == '__main__':
    unittest.main()
