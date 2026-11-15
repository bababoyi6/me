"""The reviewable release lock must agree with every production download pin."""
import json
from pathlib import Path
import re
import unittest
from release_pins import RELEASES


class ReleaseLockTests(unittest.TestCase):
    def test_bootstrap_uses_the_reviewed_release_pin(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / 'telemt-dual-hop.sh').read_text(encoding='utf-8')
        readme = (root / 'README.md').read_text(encoding='utf-8')
        tag = re.search(r'^readonly TDH_STABLE_MANAGER_TAG="(v[0-9.]+)"$', script, re.M)[1]
        digest = re.search(r'^readonly TDH_STABLE_MANAGER_SHA256="([a-f0-9]{64})"$', script, re.M)[1]
        self.assertEqual((tag, digest), ('v1.7.0', '33c90f9289375cbccf9a6480d7026a58b1bef03ba8e50421b2d7c9d919a6e2ef'))
        self.assertIn('https://raw.githubusercontent.com/bababoyi6/me/v1.7.1/telemt-dual-hop.sh', readme)
        self.assertIn('"ffa6d8edb386aa5cc833f814ca1a26c9ba1c314df44e4feccff17191940ac8a3"', readme)
        self.assertNotIn('raw.githubusercontent.com/bababoyi6/me/main/', script)

    def test_acceptance_and_native_builder_pin_the_same_active_source(self):
        root=Path(__file__).resolve().parents[1]
        lock=json.loads((root/'docs/telemt-stable-lock.json').read_text())
        pin=RELEASES[lock['tag']]
        self.assertEqual(pin,(lock['assets']['x86_64']['sha256'],lock['assets']['x86_64']['binary_sha256'],'telemt '+lock['tag']))
        builder=(root/'tools/build_stable_candidate.py').read_text()
        self.assertEqual(re.search(r"^COMMIT='([a-f0-9]{40})'",builder,re.M)[1],lock['commit'])

    def test_installer_and_release_lock_cannot_silently_diverge(self):
        root = Path(__file__).resolve().parents[1]
        lock = json.loads((root/'docs/telemt-stable-lock.json').read_text())
        script = (root/'telemt-dual-hop.sh').read_text(encoding='utf-8')
        values = dict(re.findall(r'^readonly (TELEMT_[A-Z0-9_]+)="([^"$]+)"$', script, re.M))
        self.assertEqual(values['TELEMT_VERSION'], lock['tag'])
        self.assertEqual(values['TELEMT_COMMIT'], lock['commit'])
        for arch, suffix in (('x86_64', 'AMD64'), ('aarch64', 'ARM64')):
            asset = lock['assets'][arch]
            self.assertEqual(values['TELEMT_SHA256_'+suffix], asset['sha256'])
            self.assertEqual(values['TELEMT_BINARY_SHA256_'+suffix], asset['binary_sha256'])
            self.assertEqual(asset['url'], f'https://github.com/telemt/telemt/releases/download/{lock["tag"]}/{asset["name"]}')
        self.assertRegex(lock['commit'], r'^[a-f0-9]{40}$')
        self.assertNotIn('/latest/', script)
