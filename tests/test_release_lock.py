"""The reviewable release lock must agree with every production download pin."""
import hashlib
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
        self.assertEqual((tag, digest), ('v1.7.1', 'ffa6d8edb386aa5cc833f814ca1a26c9ba1c314df44e4feccff17191940ac8a3'))
        self.assertIn('https://raw.githubusercontent.com/bababoyi6/me/v1.7.2/telemt-dual-hop.sh', readme)
        self.assertIn('"0ba07d571e33ce3e41e992c8c5f5d99663507f44c09c99c5983fb973ea0d02dc"', readme)
        self.assertEqual(hashlib.sha256((root / 'telemt-dual-hop.sh').read_bytes()).hexdigest(),
                         '0ba07d571e33ce3e41e992c8c5f5d99663507f44c09c99c5983fb973ea0d02dc')
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
