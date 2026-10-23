"""Simulate APT boundaries; never call the system package manager."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "telemt-dual-hop.sh"


class PackageRecoveryTests(unittest.TestCase):
    def invoke(self, messages, operation="update -qq", expected=0, expire=False):
        with tempfile.TemporaryDirectory(prefix="tdh-apt-test-") as name:
            base = Path(name)
            for i, message in enumerate(messages, 1):
                (base / f"error-{i}").write_text(message)
            env = dict(os.environ, TDH_TEST_SCRIPT=str(SCRIPT), TDH_APT_TEST=str(base),
                       TDH_APT_EXPIRE="1" if expire else "0")
            script = '''set -Eeuo pipefail
TDH_SOURCE_ONLY=1 source "$TDH_TEST_SCRIPT"
apt-get() {
  local count=0
  [[ ! -f $TDH_APT_TEST/count ]] || read -r count <"$TDH_APT_TEST/count"
  count=$((count + 1))
  printf '%s\\n' "$count" >"$TDH_APT_TEST/count"
  printf '%s\\n' "$@" >>"$TDH_APT_TEST/options"
  [[ $LC_ALL == C && $DEBIAN_FRONTEND == noninteractive ]]
  if [[ -f $TDH_APT_TEST/error-$count ]]; then
    cat "$TDH_APT_TEST/error-$count" >&2
    return 100
  fi
}
sleep() {
  [[ $1 == 5 ]]
  printf 'wait\\n' >>"$TDH_APT_TEST/waits"
  if [[ $TDH_APT_EXPIRE == 1 ]]; then SECONDS=601; fi
}
apt_get_with_lock_wait ''' + operation
            result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
            calls = int((base / "count").read_text())
            options = (base / "options").read_text()
            self.assertIn("Acquire::Retries=2", options)
            self.assertIn("Acquire::https::Timeout=30", options)
            for unsafe in ("AllowUnauthenticated", "AllowInsecure", "Check-Valid-Until=false", "trusted=yes"):
                self.assertNotIn(unsafe, options)
            self.assertEqual("APT::Update::Error-Mode=any" in options, operation.startswith("update"))
            return calls, result

    def test_real_mirror_size_error_retries_with_full_apt_validation(self):
        calls, result = self.invoke(["File has unexpected size (3908884 != 3908872). Mirror sync in progress?"])
        self.assertEqual(calls, 2)
        self.assertIn("1/2", result.stderr)

    def test_persistent_checksum_mismatch_stops_after_three_attempts(self):
        calls, _ = self.invoke(["Hash Sum mismatch"] * 3, expected=100)
        self.assertEqual(calls, 3)

    def test_transient_resolution_failure_is_bounded(self):
        calls, _ = self.invoke(["Temporary failure resolving 'archive.ubuntu.com'"] * 3, expected=100)
        self.assertEqual(calls, 3)

    def test_invalid_signature_and_unknown_key_fail_without_retry(self):
        for error in ("BADSIG invalid signature", "NO_PUBKEY 12345", "The repository is not signed"):
            calls, _ = self.invoke([error], expected=100)
            self.assertEqual(calls, 1)

    def test_installer_does_not_repeat_failed_package_configuration(self):
        calls, _ = self.invoke(["dpkg configuration failure: File has unexpected size"],
                               operation="install -y synthetic", expected=100)
        self.assertEqual(calls, 1)

    def test_lock_wait_and_mirror_retry_both_obey_deadline(self):
        for error in ("Could not get lock", "File has unexpected size"):
            calls, _ = self.invoke([error] * 3, expected=100, expire=True)
            self.assertEqual(calls, 2)


if __name__ == "__main__":
    unittest.main()
