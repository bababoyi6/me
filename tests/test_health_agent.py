"""Fail closed on malformed readiness replies and stale monitor results."""
import ast
import io
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def functions():
    source = (ROOT / "tools/health_agent.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    env = dict(json=json, urllib=urllib, API_TOKEN="synthetic-test-token", API_READY=True, DC_READY=True,
               API_CHECKED=99, DC_CHECKED=80, API_MAX_AGE=5, DC_MAX_AGE=25, READY_LOCK=threading.Lock())
    exec(compile(tree, "health-agent-functions", "exec"), env)
    return env


class HealthAgentTests(unittest.TestCase):
    def test_embedded_agent_is_exact_reviewed_source(self):
        installer = (ROOT / "telemt-dual-hop.sh").read_text(encoding="utf-8")
        embedded = installer.split("  cat >/usr/local/lib/telemt-dual-hop/health_agent.py <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        self.assertEqual(embedded + "\n", (ROOT / "tools/health_agent.py").read_text(encoding="utf-8"))

    def test_stale_or_missing_monitors_cannot_admit_connections(self):
        env = functions()
        self.assertTrue(env["ready"](100))
        for field, value in (("API_CHECKED", 94), ("DC_CHECKED", 74), ("API_CHECKED", None),
                             ("DC_CHECKED", None), ("API_CHECKED", 101), ("API_READY", False), ("DC_READY", False)):
            with self.subTest(field=field, value=value):
                sample = functions()
                sample[field] = value
                self.assertFalse(sample["ready"](100))

    def test_bad_health_payloads_fail_without_killing_monitor(self):
        env = functions()
        for payload in ([], None, True, "text", {"ok": True, "data": []}, {"ok": True, "data": None},
                        {"ok": True, "data": {"ready": 1}}, {"ok": False, "data": {"ready": True}}):
            with self.subTest(payload=payload):
                response = io.BytesIO(json.dumps(payload).encode())
                response.status = 200
                self.assertFalse(env["healthy"](SimpleNamespace(open=lambda *a, **k: response), 19091))
        response = io.BytesIO(b'{"ok":true,"data":{"ready":true}}')
        response.status = 200
        self.assertTrue(env["healthy"](SimpleNamespace(open=lambda *a, **k: response), 19091))

    def test_oversized_response_is_bounded_and_rejected(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b" " * 16385
        self.assertFalse(functions()["healthy"](SimpleNamespace(open=lambda *a, **k: response), 19091))
        response.read.assert_called_once_with(16385)

    def test_all_dc_policy_and_two_round_recovery_hysteresis(self):
        # Drive the actual monitor with a fake clock; no sockets or wall-clock sleeps.
        env = functions()
        outcomes = iter([True, True, False, False, True, False, True, True])
        current = [False]
        observed = []
        class Executor:
            def __init__(self, **kwargs):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def map(self, fn, addresses):
                self.addresses = list(addresses)
                return [True] * 4 + [next(outcomes)]
        class Finished(Exception):
            pass
        def sleep(seconds):
            self.assertEqual(seconds, 10)
            observed.append(env["DC_READY"])
            if len(observed) == 8:
                raise Finished()
        env.update(DC_READY=False, DCS=tuple(range(5)), probe_dc=lambda x: current[0],
                   concurrent=SimpleNamespace(futures=SimpleNamespace(ThreadPoolExecutor=Executor)),
                   time=SimpleNamespace(monotonic=lambda: 100, sleep=sleep))
        with self.assertRaises(Finished):
            env["monitor_dcs"]()
        self.assertEqual(observed, [False, True, True, False, False, False, False, True])
