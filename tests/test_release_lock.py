"""The reviewable release lock must agree with every production download pin."""
import json
from pathlib import Path
import re
import unittest


class ReleaseLockTests(unittest.TestCase):
    def test_installer_and_release_lock_cannot_silently_diverge(self):
        root = Path(__file__).resolve().parents[1]
        lock = json.loads((root/'docs/telemt-stable-lock.json').read_text())
        script = (root/'telemt-dual-hop.sh').read_text()
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
