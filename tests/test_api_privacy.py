"""Private API credentials stay on loopback and outside process arguments."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "synthetic-api-token-for-regression-only"
DOMAIN = "apple.com"
LINK = "https://t.me/proxy?server=203.0.113.10&port=443&secret=synthetic" + DOMAIN.encode().hex()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.requests.append({"authorization": self.headers.get("Authorization"), "path": self.path})
        body = json.dumps({"data": [{"username": "line_a", "links": {"tls": [LINK]}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


@unittest.skipUnless(shutil.which("curl") and shutil.which("jq"), "requires curl and jq")
class ApiPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="tdh-api-")
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.api, self.trap = [ThreadingHTTPServer(("127.0.0.1", 0), Handler) for _ in range(2)]
        for server in (self.api, self.trap):
            server.requests = []
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
        proxy = "http://127.0.0.1:" + str(self.trap.server_port)
        self.env = dict(os.environ, TDH_BASE=str(self.base), TDH_TEST_SCRIPT=str(ROOT / "telemt-dual-hop.sh"),
                        TDH_TEST_TOKEN=TOKEN, TDH_TEST_API_PORT=str(self.api.server_port),
                        http_proxy=proxy, HTTP_PROXY=proxy, all_proxy=proxy, ALL_PROXY=proxy,
                        no_proxy="", NO_PROXY="", CURL_HOME=str(self.base))

    def query(self, prelude="", success=True):
        source = '''set -Eeuo pipefail
TDH_SOURCE_ONLY=1 source "$TDH_TEST_SCRIPT"
API_TOKEN=$TDH_TEST_TOKEN
'''
        result = subprocess.run(["bash", "-c", source + prelude + '\nquery_telemt_link "$TDH_TEST_API_PORT" line_a apple.com'],
                                env=self.env, capture_output=True, text=True, timeout=10)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), LINK)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def test_legacy_counterexample_reaches_environment_proxy(self):
        self.env["TDH_LEGACY_QUERY"] = str(ROOT / "tests/fixtures/legacy-query-link.sh")
        self.query('source "$TDH_LEGACY_QUERY"')
        self.assertEqual(self.api.requests, [])
        self.assertEqual(self.trap.requests[0]["authorization"], "Bearer " + TOKEN)

    def test_current_helper_bypasses_proxies_and_ignores_curlrc(self):
        trace = self.base / "must-not-exist.trace"
        (self.base / ".curlrc").write_text(f'proxy = "http://127.0.0.1:{self.trap.server_port}"\ntrace-ascii = "{trace}"\n')
        self.query()
        self.assertEqual(self.trap.requests, [])
        self.assertEqual(self.api.requests[0]["authorization"], "Bearer " + TOKEN)
        self.assertEqual(self.api.requests[0]["path"], "/v1/users")
        self.assertFalse(trace.exists())

    def test_authentication_header_never_appears_in_curl_arguments(self):
        self.query('''curl() {
  printf '%s\\0' "$@" >"$TDH_BASE/curl-arguments"
  command curl "$@"
}
''')
        arguments = (self.base / "curl-arguments").read_bytes()
        self.assertNotIn(TOKEN.encode(), arguments)
        self.assertNotIn(b"Authorization", arguments)
        self.assertTrue(arguments.startswith(b"-q\x00"))

    def test_header_control_characters_are_rejected_before_network(self):
        result = self.query("API_TOKEN=$'synthetic\\r\\nInjected: header'", success=False)
        self.assertEqual(self.api.requests, [])
        self.assertEqual(self.trap.requests, [])
        self.assertNotIn("Injected", result.stdout + result.stderr)

    def test_invalid_port_is_rejected_before_network(self):
        self.env["TDH_TEST_API_PORT"] = "invalid@other-host"
        self.query(success=False)
        self.assertEqual(self.api.requests, [])
        self.assertEqual(self.trap.requests, [])

    def test_quotes_and_backslashes_preserve_the_exact_header(self):
        self.env["TDH_TEST_TOKEN"] = 'synthetic-quote"-backslash\\-token'
        self.query()
        self.assertEqual(self.api.requests[0]["authorization"], "Bearer " + self.env["TDH_TEST_TOKEN"])
        self.assertEqual(self.trap.requests, [])

    def test_link_readiness_failure_never_dumps_service_logs(self):
        result = subprocess.run(["bash", "-c", '''set -Eeuo pipefail
TDH_SOURCE_ONLY=1 source "$TDH_TEST_SCRIPT"
query_telemt_link() { return 1; }
sleep() { :; }
journalctl() { printf 'SYNTHETIC_SECRET_FROM_JOURNAL'; touch "$TDH_BASE/log-read"; }
wait_for_link 19091 line_a apple.com
'''], env=self.env, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("SYNTHETIC_SECRET", result.stdout + result.stderr)
        self.assertFalse((self.base / "log-read").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
