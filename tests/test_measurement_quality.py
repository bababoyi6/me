"""Reject misleading summaries observed on the disposable Ubuntu test VPS."""
import importlib.util
from pathlib import Path
import unittest
import subprocess
import sys

spec = importlib.util.spec_from_file_location("iperf_matrix", Path(__file__).resolve().parents[1] / "tools/iperf_matrix.py")
iperf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(iperf)


class MeasurementQualityTests(unittest.TestCase):
    def test_stream_probe_cli_is_bounded_before_network_setup(self):
        probe = Path(__file__).with_name("stream_probe.py")
        options = {}
        if sys.platform == "linux":
            import resource
            def bound_memory():
                resource.setrlimit(resource.RLIMIT_AS, (128 * 1024**2, 128 * 1024**2))
            options["preexec_fn"] = bound_memory
        for flags, expected in ((["--help"], 0), (["--size", "100000001"], 2), (["--rate", "0"], 2)):
            result = subprocess.run([sys.executable, str(probe)] + flags, capture_output=True, timeout=5, **options)
            self.assertEqual(result.returncode, expected)
            self.assertLess(len(result.stdout) + len(result.stderr), 4096)

    def test_real_duration_mismatch_is_not_accepted_as_path_throughput(self):
        result = iperf.checked_metrics({"sum_sent": {"seconds": 5.999983, "retransmits": 0},
                                      "sum_received": {"seconds": 16.213048, "bits_per_second": 36643446.1922274}}, 6)
        self.assertFalse(result["timing_consistent"])
        self.assertAlmostEqual(result["receiver_mbps"], 36.6434461922274)

    def test_small_receiver_timing_difference_is_expected(self):
        result = iperf.checked_metrics({"sum_sent": {"seconds": 8, "retransmits": 3},
                                      "sum_received": {"seconds": 8.15, "bits_per_second": 20000000}}, 8)
        self.assertTrue(result["timing_consistent"])

    def test_nonfinite_and_nonpositive_values_are_rejected(self):
        for value in (float("nan"), float("inf"), -1, 0, True, "8"):
            with self.assertRaises(ValueError):
                iperf.checked_metrics({"sum_sent": {"seconds": value},
                                       "sum_received": {"seconds": 8, "bits_per_second": 20000000}}, 8)
