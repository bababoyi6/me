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

    def bash(self, body, success=True, prelude="", timeout=20, script=SCRIPT):
        setup = '''
set -Eeuo pipefail
export TDH_SOURCE_ONLY=1
source "$TDH_TEST_SCRIPT"
unset TDH_SOURCE_ONLY
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
'''
        env = dict(self.env, TDH_TEST_SCRIPT=str(script), TDH_TEST_HAPROXY=HAPROXY)
        result = subprocess.run(["bash", "-c", setup + prelude + "\n" + body], env=env,
                                text=True, capture_output=True, timeout=timeout)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

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

    def test_report_distinguishes_cpu_steal_and_memory_pressure(self):
        source = SCRIPT.read_text().split('timeout 55 python3 - "$seconds" "$TDH_STATE" <<\'PY\'\n', 1)[1].split("\nPY\n", 1)[0]
        tree = ast.parse(source)
        namespace = {}
        definitions = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom, ast.FunctionDef))]
        exec(compile(ast.Module(body=definitions, type_ignores=[]), "report", "exec"), namespace)
        usage = namespace["cpu_usage"]([0] * 8, [10, 0, 10, 60, 0, 0, 0, 20])
        self.assertEqual(usage, {"busy_percent": 20, "steal_percent": 20})
        self.assertIsNone(namespace["cpu_usage"]([10] * 8, [0] * 8)["busy_percent"])
        namespace["read"] = lambda path: "oom_kill 2\npgmajfault 5\npswpin 0\npswpout 0\nSYNTHETIC_SECRET 123\n" if path == "/proc/vmstat" else ""
        self.assertEqual(namespace["counters"](), {"Vm.oom_kill": 2, "Vm.pgmajfault": 5, "Vm.pswpin": 0, "Vm.pswpout": 0})

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

    def test_report_preserves_unofficial_core_label_and_rejects_unbounded_output(self):
        source = SCRIPT.read_text().split('timeout 55 python3 - "$seconds" "$TDH_STATE" <<\'PY\'\n', 1)[1].split("\nPY\n", 1)[0]
        tree = ast.parse(source)
        definitions = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom, ast.FunctionDef))]
        namespace = {}
        exec(compile(ast.Module(body=definitions, type_ignores=[]), "report", "exec"), namespace)
        cases = (("telemt 3.5.5\n", "3.5.5"), ("telemt 3.5.5+tdh.audit2\n", "3.5.5+tdh.audit2"),
                 ("telemt 3.5.5-rc.1", "3.5.5-rc.1"), ("telemt 3.5.5\nSENSITIVE", None),
                 ("telemt 3.5.5+" + "x" * 1000, None), ("unrecognized", None))
        for output, expected in cases:
            with self.subTest(output=output[:80]):
                namespace["command"] = lambda *_, text=output: text
                self.assertEqual(namespace["telemt_version"](), expected)

    def test_socket_summary_aggregates_without_endpoint_disclosure(self):
        source = SCRIPT.read_text().split('timeout 55 python3 - "$seconds" "$TDH_STATE" <<\'PY\'\n', 1)[1].split("\nPY\n", 1)[0]
        tree = ast.parse(source)
        definitions = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom, ast.FunctionDef))]
        namespace = {}
        exec(compile(ast.Module(body=definitions, type_ignores=[]), "report", "exec"), namespace)
        namespace["command"] = lambda *_: '''0 1024 10.77.1.1:40000 10.77.1.2:24431 users:(("haproxy",pid=10,fd=4))
\t skmem:(r0,rb131072,t0,tb524288) bbr rtt:42.5/2.1 cwnd:30 snd_wnd:65536 rcv_space:30000 rwnd_limited:210ms(12%) sndbuf_limited:15ms(1%)
0 0 10.77.1.2:24431 10.77.1.1:40001 users:(("telemt",pid=11,fd=6))
\t skmem:(r0,rb524288,t0,tb524288) bbr rtt:100.0/1 cwnd:20
0 0 10.77.1.2:24431 10.77.1.1:40002 users:(("telemt",pid=11,fd=7))
\t skmem:(r0,rb262144,t0,tb262144) bbr rtt:110.0/1 cwnd:10
0 0 203.0.113.10:443 198.51.100.1:50000 users:(("haproxy",pid=10,fd=5))
\t cubic rtt:120.0/5 cwnd:10
'''
        result = namespace["sockets"]()
        self.assertEqual(result["entry-backend"]["send_q_bytes"], 1024)
        self.assertEqual(result["entry-backend"]["rtt_ms_median"], 42.5)
        self.assertEqual(result["entry-backend"]["bbr"], 1)
        self.assertEqual(result["entry-backend"]["recv_buffer_limit_bytes_median"], 131072)
        self.assertEqual(result["entry-backend"]["send_buffer_limit_bytes_median"], 524288)
        self.assertEqual(result["entry-backend"]["snd_wnd_bytes_median"], 65536)
        self.assertEqual(result["entry-backend"]["rcv_space_bytes_median"], 30000)
        self.assertEqual(result["entry-backend"]["rwnd_limited_ms_median"], 210)
        self.assertEqual(result["entry-backend"]["sndbuf_limited_ms_median"], 15)
        self.assertEqual(result["backend-entry"]["recv_buffer_limit_bytes_min"], 262144)
        self.assertEqual(result["backend-entry"]["recv_buffer_limit_bytes_max"], 524288)
        self.assertEqual(result["backend-entry"]["recv_buffer_limit_bytes_median"], 393216)
        self.assertIsNone(result["user-entry"]["snd_wnd_bytes_median"])
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
        manager.write_text(SCRIPT.read_text().replace('TDH_VERSION="1.5.0"', 'TDH_VERSION="1.3.1"'))
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

    def test_oversized_manager_download_is_stopped_and_cleaned_before_execution(self):
        manager = self.base / "manager"
        manager.write_bytes(b"existing-manager-must-survive\n")
        before = manager.read_bytes()
        (self.base / "download").write_bytes(SCRIPT.read_bytes() + b'\n#' + b'x' * 1048576 + b'\n')
        self.bash("update_manager", success=False, prelude='''
curl() {
  printf '%s' "${@: -1}" >"$TDH_BASE/partial-path"
  cp "$TDH_BASE/download" "${@: -1}"
}
install_shortcut() { printf 'unexpected' >"$TDH_BASE/activated"; }
''')
        self.assertEqual(manager.read_bytes(), before)
        self.assertFalse((self.base / "activated").exists())
        self.assertFalse(Path((self.base / "partial-path").read_text()).exists())
        self.assertFalse((self.base / "systemctl.log").exists())

    def test_real_legacy_updater_preserves_all_connection_information(self):
        legacy = ROOT / "tests/fixtures/manager-1.3.1-updater.sh"
        previous_state = self.state.read_bytes()
        # Synthetic identities only. These files must remain byte-identical.
        files = {
            "keys/entry.key": b"SYNTHETIC_PRIVATE_KEY_DO_NOT_CHANGE\n",
            "telemt/telemt.toml": b'[access.users]\nline_a = "SYNTHETIC_SECRET"\n',
            "links.txt": b"https://t.me/proxy?server=203.0.113.10&port=443&secret=synthetic\n",
            "wireguard.conf": b"AllowedIPs = 10.77.1.2/32\n",
        }
        for name, content in files.items():
            path = self.base / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        (self.base / "download").write_bytes(SCRIPT.read_bytes())
        for role in ("entry", "backend1", "backend2"):
            (self.base / "manager").write_bytes(legacy.read_bytes())
            prelude = f'''load_state() {{ TDH_ROLE={role}; CONFIG_SCHEMA=5; PORT_A=443; PORT_B=443; }}
curl() {{ cp "$TDH_BASE/download" "${{@: -1}}"; }}
install_shortcut() {{ :; }}
'''
            self.bash("update_manager", script=legacy, prelude=prelude)
            self.assertEqual((self.base / "manager").read_bytes(), SCRIPT.read_bytes())
            self.assertEqual(self.state.read_bytes(), previous_state)
            self.assertEqual(self.config.read_bytes(), self.original)
            for name, content in files.items():
                self.assertEqual((self.base / name).read_bytes(), content)
            self.assertFalse((self.base / "systemctl.log").exists())

    def test_shortcut_failure_does_not_replace_manager(self):
        manager = self.base / "manager"
        manager.write_text("old manager bytes\n")
        old = manager.read_bytes()
        (self.base / "download").write_bytes(SCRIPT.read_bytes())
        self.bash("update_manager", success=False, prelude='''
curl() { cp "$TDH_BASE/download" "${@: -1}"; }
install_shortcut() { return 1; }
''')
        self.assertEqual(manager.read_bytes(), old)
        self.assertEqual(self.config.read_bytes(), self.original)

    def test_stalled_selftest_cannot_replace_manager_or_rollback(self):
        manager = self.base / "manager"
        manager.write_text("old manager bytes\n")
        before = manager.read_bytes()
        (self.base / "download").write_bytes(SCRIPT.read_bytes())
        guard = '''
curl() { cp "$TDH_BASE/download" "${@: -1}"; }
install_shortcut() { :; }
timeout() {
  [[ $1 == 60 && $2 == bash && ${4:-} == --self-test ]] || return 99
  printf 'selftest deadline applied\\n' >>"$TDH_BASE/deadlines"
  return 124
}
'''
        self.bash("update_manager", success=False, prelude=guard)
        self.assertEqual(manager.read_bytes(), before)
        (self.base / "manager.previous").write_bytes(SCRIPT.read_bytes())
        self.bash("rollback_manager", success=False, prelude=guard)
        self.assertEqual(manager.read_bytes(), before)
        self.assertEqual((self.base / "deadlines").read_text().splitlines(), ["selftest deadline applied"] * 2)
        self.assertFalse((self.base / "systemctl.log").exists())

    def test_release_has_no_production_performance_apply_path(self):
        self.assertNotIn("performance_apply()", SCRIPT.read_text())
        self.assertNotIn("perf-apply)", SCRIPT.read_text())

    def test_runtime_templates_match_production_baseline(self):
        import hashlib
        manifest = json.loads((ROOT / "tests/fixtures/runtime-template-hashes.json").read_text())
        text = SCRIPT.read_text()
        for name, expected in manifest["functions"].items():
            start = text.index(name + "() {\n")
            end = text.index("\n}\n", start) + 3
            self.assertEqual(hashlib.sha256(text[start:end].encode()).hexdigest(), expected, name)

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
