"""Candidate archive trust and install-format compatibility; synthetic binary only."""
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import candidate_bundle as package
from core_install import archive_payload
sys.path.pop(0)


class CandidateBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        binary=bytearray(128);binary[:6]=b'\x7fELF\x02\x01';binary[18:20]=b'\x3e\x00'
        self.binary=bytes(binary);self.binary_sha=package.sha256(self.binary)
        self.native=json.loads((ROOT/'docs/bulk-core-manifest-20261024.json').read_text())
        for info in self.native['binaries'].values():
            info.update(sha256=self.binary_sha,bytes=len(binary))

    def pair(self, mutate=None):
        files={'LICENSE':(ROOT/'patches/TELEMT-LICENSE.txt').read_bytes(),'NOTICE.txt':b'Synthetic native fixture'}
        source_paths={'base.patch':'patches/telemt-3.5.5-relay-backpressure.patch',
                      'build_relay_candidate.py':'tools/build_relay_candidate.py'}
        for name in package.SOURCES:
            files[name]=(ROOT/source_paths.get(name,'patches/'+name)).read_bytes()
        for name in self.native['binaries']:
            files['telemt-'+name]=self.binary
        files['manifest.json']=json.dumps(self.native).encode()
        if mutate:
            mutate(files)
        archive=self.root/'pair.tar.gz'
        with tarfile.open(archive,'w:gz') as target:
            for name,content in files.items():
                info=tarfile.TarInfo(name);info.size=len(content)
                target.addfile(info,io.BytesIO(content))
        return archive

    def test_unpinned_pair_is_rejected_before_output(self):
        archive=self.pair()
        with self.assertRaisesRegex(ValueError,'pinned'):
            package.bundle(archive,self.root/'output.tar.gz')
        self.assertFalse((self.root/'output.tar.gz').exists())

    def test_determinism_complete_replay_sources_and_real_install_format(self):
        archive=self.pair()
        with patch.multiple(package,PAIR_SHA256=package.sha256(archive.read_bytes()),CORE_SHA256=self.binary_sha):
            first=package.bundle(archive,self.root/'first.tar.gz')
            second=package.bundle(archive,self.root/'second.tar.gz')
        self.assertEqual(first,second)
        binary,docs=archive_payload(self.root/'first.tar.gz',first['bundle_sha256'],package.VERSION,self.binary_sha,True)
        self.assertEqual(binary,self.binary)
        self.assertEqual(docs['TELEMT-LICENSE.txt'],(ROOT/'patches/TELEMT-LICENSE.txt').read_bytes())
        kit=json.loads(docs['build-provenance.json'])['source_kit']
        self.assertEqual(set(kit),set(package.SOURCES)|{'flushed_copy.rs'})
        for item in kit.values():
            self.assertEqual(package.sha256(item['text_utf8'].encode()),item['sha256'])
        self.assertIn(b'Additional source helpers',docs['NOTICE.txt'])
        self.assertFalse(first['published'])
        self.assertFalse(first['updater_enabled'])

    def test_wrong_provenance_or_extra_members_are_refused(self):
        self.native['source_commit']='0'*40
        for mutate in (None,lambda files:files.update({'../escape':b'unsafe'})):
            with self.subTest(extra=mutate is not None):
                archive=self.pair(mutate)
                with patch.multiple(package,PAIR_SHA256=package.sha256(archive.read_bytes()),CORE_SHA256=self.binary_sha):
                    with self.assertRaises(ValueError):
                        package.bundle(archive,self.root/'output.tar.gz')
                self.assertFalse((self.root/'output.tar.gz').exists())
