"""Source completeness is required before a native candidate can be distributed."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import stable_core_bundle as module


class StableCoreBundleTests(unittest.TestCase):
    def test_new_helpers_are_in_the_actual_distributed_patch(self):
        helpers={'low_rtt_receive.rs':b'first\n\nlast\n','unsent_queue_bulk.rs':b'second\n'}
        patch=module.complete_patch(b'diff --git a/base b/base\n',helpers)
        self.assertEqual(patch.count(b'new file mode 100644'),2)
        self.assertIn(b'@@ -0,0 +1,3 @@\n+first\n+\n+last\n',patch)
        for name in helpers:self.assertIn(('+++ b/src/transport/socket/'+name).encode(),patch)

    def test_missing_added_source_or_changed_line_endings_are_refused(self):
        for helpers in ({'low_rtt_receive.rs':b'first\n'},
            {'low_rtt_receive.rs':b'first\r\n','unsent_queue_bulk.rs':b'second\n'},
            {'low_rtt_receive.rs':b'first','unsent_queue_bulk.rs':b'second\n'}):
            with self.assertRaises(ValueError):module.complete_patch(b'base\n',helpers)

    def test_unpinned_input_never_creates_an_output(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'manifest.json').write_bytes(b'{}')
            target=root/'candidate.tar.gz'
            with self.assertRaisesRegex(ValueError,'checksum mismatch'):module.bundle(root,target)
            self.assertFalse(target.exists())
