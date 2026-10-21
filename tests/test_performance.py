#!/usr/bin/env python3
"""Unprivileged regression tests; all mutation paths point into temporary dirs."""
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "telemt-dual-hop.sh"
HAPROXY = os.environ.get("TDH_TEST_HAPROXY", shutil.which("haproxy") or "")


class PerformanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="tdh-test-")
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.config = self.base / "haproxy.cfg"
        self.state = self.base / "state.env"
        self.env = dict(os.environ, TDH_BASE=str(self.base), TDH_STATE=str(self.state),
                        TDH_CONFIG_DIR=str(self.base / "telemt"), TDH_WORK_ROOT=str(self.base / "work"),
                        TDH_MANAGER=str(self.base / "manager"), TDH_SHORTCUT=str(self.base / "a"))
        self.state.write_text("TDH_ROLE=entry\nCONFIG_SCHEMA=5\nPORT_A=443\nPORT_B=443\n")
        self.config.write_text("""global
    maxconn 1000
defaults
    mode tcp
    timeout connect 1s
    timeout client 1s
    timeout server 1s
frontend mtp_tls
    bind 127.0.0.1:24490
    tcp-request inspect-delay 3s
    tcp-request content accept if { req.ssl_hello_type 1 }
    use_backend telemt_b if { req.ssl_sni -i gs.apple.com }
    default_backend telemt_a
backend telemt_a
    server vps1 127.0.0.1:24491 send-proxy-v2
    server vps2 127.0.0.1:24492 send-proxy-v2 backup
backend telemt_b
    server vps2 127.0.0.1:24492 send-proxy-v2
    server vps1 127.0.0.1:24491 send-proxy-v2 backup
""")
        self.original = self.config.read_bytes()

    def bash(self, body, success=True, prelude="", timeout=20):
        setup = '''
set -Eeuo pipefail
export TDH_SOURCE_ONLY=1
source "$TDH_TEST_SCRIPT"
unset TDH_SOURCE_ONLY
TDH_REAL_WAIT=$(declare -f wait_haproxy_generation)
load_state() { TDH_ROLE=entry; CONFIG_SCHEMA=5; PORT_A=443; PORT_B=443; }
haproxy() {
    if [[ ${1:-} == -vv ]]; then printf '+LINUX_SPLICE\\n';
    else "$TDH_TEST_HAPROXY" "$@"; fi
}
systemctl() { printf '%s\\n' "$*" >>"$TDH_BASE/systemctl.log"; }
timeout() {
  if [[ ${2:-} == systemctl ]]; then shift; "$@";
  else command timeout "$@"; fi
}
haproxy_all_telemt_up() { return 0; }
wait_haproxy_backends() { return 0; }
haproxy_worker_pid() { echo 100; }
wait_haproxy_generation() { return 0; }
'''
        env = dict(self.env, TDH_TEST_SCRIPT=str(SCRIPT), TDH_TEST_HAPROXY=HAPROXY)
        result = subprocess.run(["bash", "-c", setup + prelude + "\n" + body], env=env,
                                text=True, capture_output=True, timeout=timeout)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_candidate_preserves_existing_configuration(self):
        self.bash('render_splice_candidate "$TDH_HAPROXY_CFG" "$TDH_BASE/candidate"')
        candidate = (self.base / "candidate").read_text()
        self.assertEqual(candidate.replace("    maxpipes 256\n", "").replace("    option splice-auto\n", ""),
                         self.original.decode())
        self.assertEqual(candidate.count("option splice-auto"), 2)

    def test_reject_custom_splice_and_ambiguous_layout(self):
        for extra in ("    maxpipes 10\n", "    nosplice\n", "backend telemt_a\n"):
            self.config.write_bytes(self.original + extra.encode())
            self.bash('render_splice_candidate "$TDH_HAPROXY_CFG" "$TDH_BASE/candidate"', success=False)

    @unittest.skipUnless(HAPROXY, "requires HAProxy")
    def test_apply_and_exact_rollback(self):
        self.config.chmod(0o640)
        self.bash("performance_apply")
        self.assertIn("option splice-auto", self.config.read_text())
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o640)
        self.assertEqual((self.base / "performance-backup/before.cfg").read_bytes(), self.original)
        self.assertEqual((self.base / "performance-backup").stat().st_mode & 0o777, 0o700)
        self.bash("performance_rollback")
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertFalse((self.base / "performance-backup").exists())
        log = (self.base / "systemctl.log").read_text()
        self.assertNotIn("restart", log)
        self.assertNotIn("stop", log)

    @unittest.skipUnless(HAPROXY, "requires HAProxy")
    def test_failed_reload_automatically_restores(self):
        self.bash("performance_apply", success=False, prelude='''
systemctl() {
  if [[ $1 == reload && ! -f $TDH_BASE/failed ]]; then
    touch "$TDH_BASE/failed"; return 1
  fi
  return 0
}
timeout() { shift; "$@"; }
''')
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertFalse((self.base / "performance-backup").exists())

    @unittest.skipUnless(HAPROXY, "requires HAProxy")
    def test_post_reload_health_failure_restores(self):
        self.bash("performance_apply", success=False, prelude="wait_haproxy_generation() { return 1; }")
        self.assertEqual(self.config.read_bytes(), self.original)

    @unittest.skipUnless(HAPROXY, "requires HAProxy")
    def test_reject_drift_and_repeated_apply(self):
        self.bash("performance_apply")
        self.bash("performance_apply", success=False)
        modified = self.config.read_text() + "# user change\n"
        self.config.write_text(modified)
        self.bash("performance_rollback", success=False)
        self.assertEqual(self.config.read_text(), modified)
        self.assertTrue((self.base / "performance-backup/before.cfg").exists())

    def test_reject_unhealthy_before_mutation(self):
        self.bash("performance_apply", success=False, prelude="haproxy_all_telemt_up() { return 1; }")
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertFalse((self.base / "performance-backup").exists())

    def test_resources_and_legacy_schema(self):
        self.bash('''
[[ $(calculate_connection_limit 1024 1) == 2048 ]]
[[ $(calculate_connection_limit 2048 1) == 4000 ]]
[[ $(calculate_connection_limit 65536 32) == 30000 ]]
[[ $(calculate_direct_buffer_budget 1024) == 268435456 ]]
if calculate_connection_limit 512 1; then exit 1; fi
CONFIG_SCHEMA=5 PORT_A=443 PORT_B=443 INSTALLED_VERSION=1.3.1
state_is_current
CONFIG_SCHEMA=4
if state_is_current; then exit 1; fi
''')

    def test_report_has_no_state_evaluation_or_secret_output(self):
        canary = "SENSITIVE_CANARY_PRIVATE_SECRET_123456"
        self.state.write_text(f'TDH_ROLE=entry\nSECRET_A={canary}\n$(touch "{self.base}/executed")\n')
        before = self.state.read_bytes()
        result = self.bash('performance_report 2', timeout=55)
        report = json.loads(result.stdout)
        self.assertEqual(report["role"], "entry")
        self.assertEqual(report["schema"], 1)
        self.assertNotIn(canary, result.stdout + result.stderr)
        self.assertFalse((self.base / "executed").exists())
        self.assertEqual(self.state.read_bytes(), before)
        self.assertFalse((self.base / "systemctl.log").exists())

    def test_report_rejects_unbounded_load(self):
        for invalid in ("0", "1", "31", "999999", "-1", "2;false", "02"):
            self.bash(f"performance_report '{invalid}'", success=False)

    def test_report_command_timeout_and_output_limit(self):
        source = SCRIPT.read_text().split('timeout 55 python3 - "$seconds" "$TDH_STATE" <<\'PY\'\n', 1)[1].split("\nPY\n", 1)[0]
        tree = ast.parse(source)
        definitions = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom, ast.FunctionDef))]
        namespace = {}
        exec(compile(ast.Module(body=definitions, type_ignores=[]), "report", "exec"), namespace)
        namespace["DEADLINE"] = time.monotonic() + 10
        start = time.monotonic()
        self.assertEqual(namespace["command"]("sh", "-c", "sleep 8"), "")
        self.assertLess(time.monotonic() - start, 4)
        self.assertEqual(namespace["command"]("python3", "-c", "print('x'*2000000)"), "")

    def test_api_sampling_uses_fixed_loopback_and_numeric_allowlist(self):
        from unittest.mock import patch
        source = SCRIPT.read_text().split('timeout 55 python3 - "$seconds" "$TDH_STATE" <<\'PY\'\n', 1)[1].split("\nPY\n", 1)[0]
        tree = ast.parse(source)
        definitions = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom, ast.FunctionDef))]
        namespace = {}
        exec(compile(ast.Module(body=definitions, type_ignores=[]), "report", "exec"), namespace)
        namespace["DEADLINE"] = time.monotonic() + 10
        namespace["read"] = lambda _: "ab" * 32
        outer = self
        class Connection:
            def __init__(self, host, port, timeout):
                outer.assertEqual((host, port, timeout), ("127.0.0.1", 19091, 2))
            def request(self, method, path, headers):
                outer.assertEqual((method, path), ("GET", "/v1/stats/zero/all"))
                outer.assertEqual(headers["Authorization"], "Bearer " + "ab" * 32)
            def getresponse(self):
                return self
            status = 200
            def read(self, limit):
                outer.assertEqual(limit, 262144)
                return json.dumps({"data": {"core": {"connections_total": 7, "secret": "SENSITIVE"},
                                             "upstream": {"connect_success_total": 2},
                                             "users": [{"secret": "SENSITIVE"}]}}).encode()
            def close(self):
                pass
        with patch.object(namespace["http"].client, "HTTPConnection", Connection):
            result = namespace["telemt"]()
        self.assertEqual(result, {"core": {"connections_total": 7}, "upstream": {"connect_success_total": 2}})

    def test_new_worker_required_before_success(self):
        self.bash('''
eval "$TDH_REAL_WAIT"
haproxy_worker_pid() { echo 100; }
sleep() { :; }
if wait_haproxy_generation 100; then exit 1; fi
haproxy_worker_pid() { echo 101; }
wait_haproxy_generation 100
''')

    def test_socket_summary_aggregates_without_endpoint_disclosure(self):
        source = SCRIPT.read_text().split('timeout 55 python3 - "$seconds" "$TDH_STATE" <<\'PY\'\n', 1)[1].split("\nPY\n", 1)[0]
        tree = ast.parse(source)
        definitions = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom, ast.FunctionDef))]
        namespace = {}
        exec(compile(ast.Module(body=definitions, type_ignores=[]), "report", "exec"), namespace)
        namespace["command"] = lambda *_: '''0 1024 10.77.1.1:40000 10.77.1.2:24431 users:(("haproxy",pid=10,fd=4))
\t skmem:(r0,rb131072) bbr rtt:42.5/2.1 cwnd:30
0 0 203.0.113.10:443 198.51.100.1:50000 users:(("haproxy",pid=10,fd=5))
\t cubic rtt:120.0/5 cwnd:10
'''
        result = namespace["sockets"]()
        self.assertEqual(result["entry-backend"]["send_q_bytes"], 1024)
        self.assertEqual(result["entry-backend"]["rtt_ms_median"], 42.5)
        self.assertEqual(result["entry-backend"]["bbr"], 1)
        self.assertEqual(result["user-entry"]["count"], 1)
        self.assertNotIn("203.0.113.10", json.dumps(result))

    def test_manager_rollback_preserves_state(self):
        before = self.state.read_bytes()
        previous = self.base / "manager.previous"
        previous.write_text('''#!/bin/bash
readonly TDH_CONFIG_SCHEMA="5"
readonly TDH_PROTOCOL_VERSION="1.2.1"
if [[ ${1:-} == --self-test ]]; then exit 0; fi
''')
        self.bash("rollback_manager")
        self.assertEqual((self.base / "manager").read_bytes(), previous.read_bytes())
        self.assertEqual(self.state.read_bytes(), before)
        previous.write_text(previous.read_text().replace('SCHEMA="5"', 'SCHEMA="6"'))
        self.bash("rollback_manager", success=False)

    def test_manager_update_and_failure_preserve_running_configuration(self):
        manager = self.base / "manager"
        manager.write_text(SCRIPT.read_text().replace('TDH_VERSION="1.4.0"', 'TDH_VERSION="1.3.1"'))
        old_manager = manager.read_bytes()
        old_state = self.state.read_bytes()
        download = self.base / "download"
        download.write_text(SCRIPT.read_text())
        prelude = 'curl() { cp "$TDH_BASE/download" "${@: -1}"; }\ninstall_shortcut() { :; }\n'
        self.bash("update_manager", prelude=prelude)
        self.assertEqual(manager.read_bytes(), SCRIPT.read_bytes())
        self.assertEqual((self.base / "manager.previous").read_bytes(), old_manager)
        self.bash("update_manager", prelude=prelude)
        self.assertEqual((self.base / "manager.previous").read_bytes(), old_manager)
        self.assertEqual(self.state.read_bytes(), old_state)
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertFalse((self.base / "systemctl.log").exists())
        for content in (SCRIPT.read_text().replace('SCHEMA="5"', 'SCHEMA="6"'), "invalid download"):
            download.write_text(content)
            self.bash("update_manager", success=False, prelude=prelude)
            self.assertEqual(manager.read_bytes(), SCRIPT.read_bytes())
            self.assertEqual(self.config.read_bytes(), self.original)

    def test_iperf_aggregate_cap_and_no_raw_error_output(self):
        import importlib.util
        from types import SimpleNamespace
        from unittest.mock import patch
        import contextlib
        import io
        spec = importlib.util.spec_from_file_location("iperf_matrix", ROOT / "tools/iperf_matrix.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        arguments = SimpleNamespace(peer="10.77.1.2", rounds=3, rate_mbps=20, seconds=3, cooldown=2)
        calls = []
        def fake_run(command, **kwargs):
            calls.append(command)
            streams = int(command[command.index("-P") + 1])
            per_stream = int(command[command.index("-b") + 1])
            self.assertLessEqual(streams * per_stream, 20000000)
            self.assertEqual(kwargs["timeout"], 13)
            return SimpleNamespace(returncode=1, stdout='{"error":"SENSITIVE_CANARY"}')
        output = io.StringIO()
        with patch.object(module.shutil, "which", return_value="iperf3"), \
             patch.object(module.subprocess, "run", side_effect=fake_run), \
             patch.object(module.time, "sleep"), contextlib.redirect_stdout(output):
            self.assertEqual(module.run(arguments), 1)
        self.assertEqual(len(calls), 12)
        self.assertNotIn("SENSITIVE_CANARY", output.getvalue())
        self.assertTrue(all(r["receiver_mbps"] is None for r in json.loads(output.getvalue())["summary"]))

    @unittest.skipUnless(HAPROXY, "requires HAProxy")
    def test_real_generated_config_and_telemt_security(self):
        self.bash('''
chown() { :; }
install() {
  local args=()
  while (( $# )); do
    case $1 in -o|-g) shift 2;; *) args+=("$1"); shift;; esac
  done
  command install "${args[@]}"
}
ENTRY_MAX_CONNECTIONS=2048 B1_MAX_CONNECTIONS=4000 B2_MAX_CONNECTIONS=3000 PORT_A=443
write_haproxy_config
ENTRY_PUBLIC_IP=203.0.113.10 ENTRY_WG_IP=10.77.1.1
BACKEND_MAX_CONNECTIONS=2048 BACKEND_BUFFER_BUDGET_BYTES=268435456
API_TOKEN=CANARY_API_TOKEN SECRET_A=CANARY_SECRET_A SECRET_B=CANARY_SECRET_B
mkdir -p "$TDH_CONFIG_DIR"
write_telemt_config 10.77.1.2 24431 19091 443
''')
        config = self.config.read_text()
        self.assertEqual(config.count("send-proxy-v2"), 4)
        self.assertEqual(config.count("agent-check"), 4)
        self.assertIn("maxconn 2048", config)
        self.assertNotIn("splice-auto", config)
        telemt = (self.base / "telemt/telemt.toml").read_text()
        for required in ('fast_mode = true', 'use_middle_proxy = false', 'ip = "10.77.1.2"',
                         'listen = "127.0.0.1:19091"', 'proxy_protocol_trusted_cidrs = ["10.77.1.1/32"]',
                         'read_only = true', 'classic = false', 'secure = false', 'tls = true'):
            self.assertIn(required, telemt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
