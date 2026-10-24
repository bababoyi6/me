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
    def test_binary_identity_hash_is_streamed_in_small_chunks(self):
        import hashlib
        import io
        from unittest.mock import patch
        from lab_files import file_sha256
        payload=b'x'*(2*65536+19)
        class BoundedReader(io.BytesIO):
            def read(self,size=-1):
                self_test.assertGreater(size,0)
                self_test.assertLessEqual(size,65536)
                return super().read(size)
        self_test=self
        with patch('lab_files.Path.open',return_value=BoundedReader(payload)),patch('pathlib.Path.read_bytes',side_effect=AssertionError('whole binary read')):
            self.assertEqual(file_sha256('/synthetic-core'),hashlib.sha256(payload).hexdigest())

    def test_mock_receiver_bound_is_inherited_before_accept_and_invalid_bounds_refused(self):
        import socket
        from unittest.mock import patch
        from e2e_lab import MockDc
        with self.assertRaisesRegex(ValueError,'receive buffer'):
            MockDc(receive_buffer=999999999)
        with patch('e2e_lab.threading.Thread.start'):
            dc=MockDc(receive_buffer=262144)
        try:
            with socket.create_connection(dc.server_address,timeout=2) as client:
                with dc.socket.accept()[0] as accepted:
                    self.assertEqual(accepted.getsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF),dc.receive_buffer_actual)
        finally:
            dc.server_close()

    def test_memory_stop_closes_proxy_before_executor_waits(self):
        import concurrent.futures
        import threading
        from types import SimpleNamespace
        from e2e_lab import close_on_error
        closed=threading.Event()
        resource=SimpleNamespace(abort=closed.set,close=lambda: self.fail('must abort before graceful waits'))
        with self.assertRaisesRegex(RuntimeError,'safety stop'):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool, close_on_error(resource):
                future=pool.submit(closed.wait,2)
                raise RuntimeError('safety stop')
        self.assertTrue(future.result())
        closed.clear()
        with close_on_error(resource):pass
        self.assertFalse(closed.is_set())

    def test_lab_cpu_interval_separates_steal_and_rejects_resets(self):
        from network_lab import cpu_interval
        value=cpu_interval([0]*8,[10,0,10,20,0,0,0,60])
        self.assertEqual(value['busy_percent'],20)
        self.assertEqual(value['steal_percent'],60)
        self.assertIsNone(cpu_interval([1]*8,[0]*8))

    def test_synthetic_reader_pause_is_bounded_before_connection(self):
        from e2e_lab import FullLab
        lab = FullLab.__new__(FullLab)
        lab.real_dc = False
        for direction, pause in (("D", -1), ("D", 6), ("U", 3)):
            with self.assertRaisesRegex(ValueError, "reader pause"):
                lab.transfer("apple.com", direction, 1024, 125000, read_pause=pause)
        with self.assertRaisesRegex(ValueError, "receive buffer"):
            lab.transfer("apple.com", "D", 1024, 125000, receive_buffer=999999999)
        with self.assertRaisesRegex(ValueError,'send buffer'):
            lab.transfer('apple.com','U',1024,125000,send_buffer=999999999)

    def test_high_rtt_lab_cli_is_bounded_before_resource_creation(self):
        cases = (("high_rtt_matrix.py", ["--help"], 0),
                 ('mixed_clients.py',['--help'],0),
                 ('mixed_clients.py',['--root','/never-create','--clients','101'],2),
                 ("dualhop_lab.py", ["--help"], 0),
                 ("persistent_relay_lab.py", ["--help"], 0),
                 ("install_recovery_lab.py", ["--help"], 0),
                 ("backend_endurance.py", ["--help"], 0),
                 ("backend_endurance.py", ["--root", "/never-create", "--haproxy", "/never-run", "--duration", "999999"], 2),
                 ("install_recovery_lab.py", ["--root", "/never-create", "--role", "unknown"], 2),
                 ("high_rtt_matrix.py", ["--root", "/never-create", "--rtt", "999999"], 2),
                 ("high_rtt_matrix.py", ["--root", "/never-create", "--rate-mbps", "999999"], 2),
                 ("high_rtt_matrix.py", ["--root", "/never-create", "--dc-timeout", "999999"], 2),
                 ("high_rtt_matrix.py", ["--root", "/never-create", "--dc-timeout", "25"], 2),
                 ("high_rtt_matrix.py", ["--root", "/never-create", "--rounds", "999999"], 2),
                 ("high_rtt_matrix.py", ["--root", "/never-create", "--rounds", "3"], 2),
                 ("high_rtt_matrix.py", ["--root", "/never-create", "--observe-network"], 2),
                 ("mock_dc_worker.py", ["--output", "/never-write", "--socket-timeout", "999999"], 2),
                 ("high_rtt_worker.py", ["--root", "/never-create", "--build", "auto", "--output", "/never-write", "--bytes", "999999999"], 2),
                 ("mock_dc_worker.py", ["--help"], 0))
        for name, flags, expected in cases:
            with self.subTest(name=name, flags=flags):
                result = subprocess.run([sys.executable, str(Path(__file__).with_name(name)), *flags],
                                        capture_output=True, timeout=5)
                self.assertEqual(result.returncode, expected)
                self.assertLess(len(result.stdout) + len(result.stderr), 4096)

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
