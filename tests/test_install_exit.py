"""Check interrupted transactions without touching host services or configuration."""
import os
from pathlib import Path
import select
import signal
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "telemt-dual-hop.sh"


class InstallExitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tdh-exit-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.env = dict(os.environ, TDH_TEST_SCRIPT=str(SCRIPT), TDH_TEST_DIR=str(self.base))
        self.setup = '''set -Eeuo pipefail
TDH_SOURCE_ONLY=1 source "$TDH_TEST_SCRIPT"
rollback_transaction() {
  TX_ACTIVE=0
  printf '%s\\n' "$TX_KIND" >>"$TDH_TEST_DIR/recovered"
}
'''

    def invoke(self, body, expected):
        result = subprocess.run(["bash", "-c", self.setup + body], env=self.env,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def test_explicit_exit_rolls_back_once_preserving_failure_code(self):
        self.invoke('TX_ACTIVE=1 TX_OWNER_PID=$BASHPID TX_KIND=backend1\nexit 7', 7)
        self.assertEqual((self.base / "recovered").read_text(), "backend1\n")

    def test_uncommitted_success_exit_is_failure_and_rolls_back(self):
        self.invoke('TX_ACTIVE=1 TX_OWNER_PID=$BASHPID TX_KIND=backend2\nexit 0', 1)
        self.assertEqual((self.base / "recovered").read_text(), "backend2\n")

    def test_normal_command_error_does_not_double_rollback(self):
        self.invoke('TX_ACTIVE=1 TX_OWNER_PID=$BASHPID TX_KIND=entry\nfalse', 1)
        self.assertEqual((self.base / "recovered").read_text(), "entry\n")

    def test_committed_install_is_not_rolled_back(self):
        self.invoke('TX_ACTIVE=1 TX_OWNER_PID=$BASHPID TX_KIND=entry\nTX_ACTIVE=0\nexit 0', 0)
        self.assertFalse((self.base / "recovered").exists())

    def test_successful_subshell_and_pipeline_do_not_own_parent_transaction(self):
        self.invoke('''TX_ACTIVE=1 TX_OWNER_PID=$BASHPID TX_KIND=entry
( : )
value=$(printf 'synthetic')
consume() { read -r value; [[ $value == synthetic ]]; }
printf 'synthetic\\n' | consume
[[ ! -e $TDH_TEST_DIR/recovered ]]
TX_ACTIVE=0
''', 0)
        self.assertFalse((self.base / "recovered").exists())

    def test_command_substitution_failure_is_recovered_by_parent_once(self):
        self.invoke('TX_ACTIVE=1 TX_OWNER_PID=$BASHPID TX_KIND=entry\nvalue=$(false)', 1)
        self.assertEqual((self.base / "recovered").read_text(), "entry\n")

    def test_existing_node_error_is_not_install_cleanup(self):
        self.invoke('TX_ACTIVE=0\nexit 7', 7)
        self.assertFalse((self.base / "recovered").exists())

    def test_inherited_panel_pid_cannot_terminate_an_unrelated_process(self):
        sentinel = subprocess.Popen(["sleep", "10"])
        self.env["PANEL_ACTION_PID"] = str(sentinel.pid)
        try:
            self.invoke('kill -TERM "$BASHPID"', 143)
            self.assertIsNone(sentinel.poll())
        finally:
            sentinel.terminate()
            sentinel.wait(timeout=2)

    def test_terminated_install_rolls_back(self):
        body = 'TX_ACTIVE=1 TX_OWNER_PID=$BASHPID TX_KIND=backend1\nprintf "ready\\n"\nwhile :; do sleep .1; done\n'
        process = subprocess.Popen(["bash", "-c", self.setup + body], env=self.env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertTrue(select.select([process.stdout], [], [], 3)[0])
            self.assertEqual(process.stdout.readline(), "ready\n")
            process.send_signal(signal.SIGTERM)
            output, error = process.communicate(timeout=3)
            self.assertEqual(process.returncode, 143, output + error)
            self.assertEqual((self.base / "recovered").read_text(), "backend1\n")
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=2)

    def test_menu_action_subshell_rolls_back_without_ending_panel(self):
        self.invoke('''pause_panel() { :; }
failed_install() { TX_ACTIVE=1 TX_OWNER_PID=$BASHPID TX_KIND=backend2; exit 7; }
run_panel_action failed_install
[[ $TX_ACTIVE == 0 ]]
printf 'panel alive\\n' >"$TDH_TEST_DIR/panel"
''', 0)
        self.assertEqual((self.base / "recovered").read_text(), "backend2\n")
        self.assertEqual((self.base / "panel").read_text(), "panel alive\n")

    def test_terminating_panel_waits_for_active_install_cleanup(self):
        self.interrupt_panel(signal.SIGTERM, 143)

    def test_disconnected_panel_waits_for_active_install_cleanup(self):
        self.interrupt_panel(signal.SIGHUP, 129)

    def interrupt_panel(self, sent_signal, expected):
        body = '''pause_panel() { :; }
slow_install() {
  TX_ACTIVE=1 TX_OWNER_PID=$BASHPID TX_KIND=backend2
  printf 'ready\\n'
  while :; do sleep .1; done
}
run_panel_action slow_install
'''
        process = subprocess.Popen(["bash", "-c", self.setup + body], env=self.env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertTrue(select.select([process.stdout], [], [], 3)[0])
            self.assertEqual(process.stdout.readline(), "ready\n")
            process.send_signal(sent_signal)
            output, error = process.communicate(timeout=3)
            self.assertEqual(process.returncode, expected, output + error)
            self.assertEqual((self.base / "recovered").read_text(), "backend2\n")
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=2)


if __name__ == "__main__":
    unittest.main()
