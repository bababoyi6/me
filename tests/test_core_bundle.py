"""Packaging trust boundaries and reproducibility; no core execution."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('core_bundle', ROOT/'tools/core_bundle.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CoreBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.binary = self.root/'binary'
        data = bytearray(128)
        data[:6], data[18:20] = b'\x7fELF\x02\x01', b'\x3e\x00'
        self.binary.write_bytes(data)

    def fixture(self):
        """Only tests replace the pinned candidate; production has no such CLI."""
        digest = module.sha256(self.binary.read_bytes())
        build = json.loads((ROOT/'docs/benchmarks/native-build-manifest.json').read_text())
        build['builds']['auto']['sha256'] = digest
        original = module.read_regular

        def read(path, limit):
            if Path(path).name == 'native-build-manifest.json':
                return json.dumps(build).encode()
            return original(path, limit)
        return patch.multiple(module, CORE_SHA256=digest, read_regular=read)

    def test_exact_binary_gate_before_output(self):
        with self.assertRaisesRegex(ValueError, 'exact validated'):
            module.bundle(self.binary, self.root/'output')
        self.assertEqual([p.name for p in self.root.iterdir()], ['binary'])

    def test_deterministic_archive_integrity_and_license(self):
        with self.fixture():
            first = module.bundle(self.binary, self.root/'first.tar.gz')
            second = module.bundle(self.binary, self.root/'second.tar.gz')
        self.assertEqual(first, second)
        with tarfile.open(self.root/'first.tar.gz') as archive:
            manifest = json.load(archive.extractfile('manifest.json'))
            self.assertTrue(manifest['unofficial'])
            self.assertFalse(manifest['updater_enabled'])
            self.assertEqual(set(archive.getnames()), set(manifest['files']) | {'manifest.json'})
            for name, info in manifest['files'].items():
                data = archive.extractfile(name).read()
                self.assertEqual(hashlib.sha256(data).hexdigest(), info['sha256'])
                self.assertEqual(len(data), info['bytes'])
                self.assertEqual(archive.getmember(name).mode, int(info['mode'], 8))
            self.assertIn(b'No endorsement', archive.extractfile('NOTICE.txt').read())
            self.assertEqual(archive.extractfile('TELEMT-LICENSE.txt').read(), (ROOT/'patches/TELEMT-LICENSE.txt').read_bytes())
        self.assertFalse(list(self.root.glob('*.stage-*')))

    def test_wrong_architecture_rejected_even_with_fixture_digest(self):
        data = bytearray(self.binary.read_bytes()); data[18:20] = b'\xb7\x00'
        self.binary.write_bytes(data)
        with self.fixture(), self.assertRaisesRegex(ValueError, 'x86_64 ELF'):
            module.bundle(self.binary, self.root/'output')

    def test_existing_output_and_symlink_inputs_are_not_followed(self):
        existing = self.root/'existing'; existing.write_text('keep')
        with self.fixture(), self.assertRaisesRegex(ValueError, 'overwrite'):
            module.bundle(self.binary, existing)
        self.assertEqual(existing.read_text(), 'keep')
        alias = self.root/'alias'; alias.symlink_to(self.binary)
        with self.fixture(), self.assertRaisesRegex(ValueError, 'regular'):
            module.bundle(alias, self.root/'output')

    def test_changed_patch_or_provenance_rejected(self):
        with self.fixture(), patch.object(module, 'PATCH_SHA256', '0'*64), self.assertRaisesRegex(ValueError, 'patch differs'):
            module.bundle(self.binary, self.root/'output')
        with self.fixture(), patch.object(module, 'SOURCE_COMMIT', '0'*40), self.assertRaisesRegex(ValueError, 'provenance'):
            module.bundle(self.binary, self.root/'output')


if __name__ == '__main__':
    unittest.main()
