"""Ensure the compiled regression helper is the helper shipped by the patch."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CorePatchTests(unittest.TestCase):
    def test_patch_helper_matches_rust_regression_source(self):
        patch = (ROOT / "patches/telemt-3.5.5-relay-backpressure.patch").read_bytes()
        marker = b"+++ b/src/proxy/relay/adaptive_copy/flushed_copy.rs\n"
        self.assertEqual(patch.count(marker), 1)
        section = patch.split(marker, 1)[1].split(b"--- a/", 1)[0]
        lines = section.splitlines(keepends=True)
        self.assertTrue(lines[0].startswith(b"@@ -0,0 +1,"))
        body = b"".join(line[1:] for line in lines[1:] if line.startswith(b"+"))
        self.assertEqual(body, (ROOT / "patches/flushed_copy.rs").read_bytes())
        source = (ROOT / "tests/relay-flush-regression/src/lib.rs").read_text()
        self.assertIn('#[path = "../../../patches/flushed_copy.rs"]', source)

    def test_patch_scope_and_unofficial_marking(self):
        text = (ROOT / "patches/telemt-3.5.5-relay-backpressure.patch").read_text()
        targets = [line[6:] for line in text.splitlines() if line.startswith("+++ b/")]
        self.assertEqual(set(targets), {"Cargo.toml", "Cargo.lock", "src/proxy/relay/adaptive_copy.rs",
                                       "src/proxy/relay/adaptive_copy/flushed_copy.rs",
                                       "src/transport/socket.rs", "AUDIT-MODIFICATIONS.md"})
        self.assertEqual(len(targets), 6)
        self.assertIn('+version = "3.5.5+tdh.audit2"', text)
        self.assertIn("No endorsement by Telemt maintainers", text)


if __name__ == "__main__":
    unittest.main()
