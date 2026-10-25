"""Low-rate traffic must remain active without changing payload bytes."""
import hashlib
import unittest
from unittest.mock import patch
from lab import send_payload


class PayloadPacingTests(unittest.TestCase):
    def transfer(self, size, rate):
        clock = [0.0]
        chunks, timestamps = [], []
        class Socket:
            def sendall(self, block):
                chunks.append(block); timestamps.append(clock[0])
        def sleep(seconds): clock[0] += seconds
        with patch('lab.time.monotonic', side_effect=lambda:clock[0]), patch('lab.time.sleep', side_effect=sleep):
            digest = send_payload(Socket(), size, rate)
        expected = (bytes(range(256))*((size+255)//256))[:size]
        self.assertEqual(b''.join(chunks), expected)
        self.assertEqual(digest, hashlib.sha256(expected).digest())
        return clock[0], timestamps

    def test_one_kib_per_second_does_not_create_idle_timeout_gaps(self):
        elapsed, timestamps = self.transfer(131073, 1024)
        self.assertAlmostEqual(elapsed, 131073/1024)
        self.assertLessEqual(max(b-a for a,b in zip(timestamps,timestamps[1:])), 1)

    def test_non_block_aligned_rate_preserves_the_complete_byte_pattern(self):
        elapsed, timestamps = self.transfer(65539, 1001)
        self.assertAlmostEqual(elapsed, 65539/1001)
        self.assertLessEqual(max(b-a for a,b in zip(timestamps,timestamps[1:])), 1)

    def test_unpaced_and_high_rate_integrity_is_preserved(self):
        self.assertEqual(self.transfer(131073, 0)[0], 0)
        self.assertAlmostEqual(self.transfer(131073, 12500000)[0],131073/12500000)
