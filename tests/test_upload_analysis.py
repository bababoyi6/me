"""Failed, unpaired or unlike workloads must not become advertised speedups."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("analyze_upload_ab", Path(__file__).resolve().parents[1] / "tools/analyze_upload_ab.py")
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)


class UploadAnalysisTests(unittest.TestCase):
    def sample(self):
        records = []
        for build, speed in (("flush", 100), ("auto", 75)):
            for turn in (1, 2):
                trial = {"complete": True, "streams": 4, "direction": "U", "expected_payload_bytes": 1024,
                         "link_rate_mbps": 100, "mbps": speed, "telemt_cpu_seconds": 1,
                         "maximum_telemt_rss_sampled_bytes": 1024, "small_probes": [], "socket_samples": []}
                records.append({"streams": 4, "build": build, "pair_round": turn, "exit": 0, "trial": {"rows": [trial]}})
        return {"rows": records, "rounds": 2, "payload_bytes_total_per_trial": 1024,
                "rate_mbps_per_direction": 100, "classification": "SINGLE_VPS_SYNTHETIC", "scope": "test", "complete": True}

    def test_negative_results_are_preserved(self):
        self.assertEqual(analysis.summarize(self.sample())["groups"][0]["auto_change_percent"], -25)

    def test_failed_or_duplicate_or_missing_pair_suppresses_ratio(self):
        mutations = (lambda rows: rows.pop(), lambda rows: rows[0].update(exit=124),
                     lambda rows: rows[0].update(pair_round=2),
                     lambda rows: rows[0]["trial"]["rows"][0].update(measurement_complete=False),
                     lambda rows: rows[0]["trial"]["rows"][0].update(small_probe_errors=[{"type": "TimeoutError"}]))
        for mutate in mutations:
            data = self.sample()
            mutate(data["rows"])
            group = analysis.summarize(data)["groups"][0]
            self.assertFalse(group["complete_pairs"])
            self.assertIsNone(group["auto_change_percent"])

    def test_unlike_workloads_and_nonfinite_values_are_rejected(self):
        for field, value in (("expected_payload_bytes", 2048), ("link_rate_mbps", 1000), ("direction", "D"),
                             ("mbps", float("nan")), ("mbps", True)):
            data = self.sample()
            data["rows"][0]["trial"]["rows"][0][field] = value
            with self.assertRaises(ValueError):
                analysis.summarize(data)

    def test_incomplete_batch_does_not_publish_a_ratio_even_with_successful_rows(self):
        data = self.sample()
        data['complete'] = False
        self.assertIsNone(analysis.summarize(data)['groups'][0]['auto_change_percent'])
