"""Kernel feature rejection happens before tuning state/config mutations."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import shutil

SCRIPT = Path(__file__).resolve().parents[1] / "telemt-dual-hop.sh"


class TuningPreflightTests(unittest.TestCase):
    def invoke(self, missing="", available="reno cubic bbr", apply=False, expected=0):
        with tempfile.TemporaryDirectory(prefix="tdh-kernel-test-") as name:
            root = Path(name)
            env = dict(os.environ, TDH_TEST_SCRIPT=str(SCRIPT), TDH_TEST_ROOT=str(root),
                       TDH_MISSING_MODULE=missing, TDH_AVAILABLE_CC=available)
            script = '''set -Eeuo pipefail
TDH_SOURCE_ONLY=1 source "$TDH_TEST_SCRIPT"
modprobe() { printf '%s\\n' "$1" >>"$TDH_TEST_ROOT/modules"; [[ $1 != "$TDH_MISSING_MODULE" ]]; }
sysctl() {
  [[ $* == '-n net.ipv4.tcp_available_congestion_control' ]] || { printf 'forbidden sysctl operation\\n' >&2; exit 99; }
  printf '%s\\n' "$TDH_AVAILABLE_CC"
}
save_state() { printf 'unexpected state mutation\\n' >"$TDH_TEST_ROOT/mutation"; }
install() { printf 'forbidden install operation\\n' >&2; exit 99; }
'''
            result = subprocess.run(["bash", "-c", script + ("apply_tuning" if apply else "check_tuning_support")],
                                    env=env, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
            self.assertFalse((root / "mutation").exists())
            return result

    def test_supported_kernel_passes_readiness_without_applying_tuning(self):
        self.invoke()

    def test_trimmed_or_missing_modules_reject_before_state_is_changed(self):
        for module in ("sch_fq", "tcp_bbr"):
            with self.subTest(module=module):
                self.invoke(missing=module, apply=True, expected=1)

    def test_unregistered_bbr_is_not_silently_accepted(self):
        self.invoke(available="reno cubic notbbr", apply=True, expected=1)

    @unittest.skipUnless(os.geteuid() == 0 and shutil.which('unshare'), 'private mount namespace requires root')
    def test_queue_floor_matches_listener_and_preserves_larger_operator_values(self):
        with tempfile.TemporaryDirectory(prefix='tdh-queue-test-') as name:
            root = Path(name)
            for folder in ('sysctl', 'modules'):
                (root/folder).mkdir()
            for current, expected in ((1024,4096), (65535,65535)):
                env = dict(os.environ, TDH_TEST_SCRIPT=str(SCRIPT), TDH_QUEUE_ROOT=name, TDH_QUEUE_CURRENT=str(current))
                child = r'''
set -Eeuo pipefail
mount --make-rprivate /
mount --bind "$TDH_QUEUE_ROOT/sysctl" /etc/sysctl.d
mount --bind "$TDH_QUEUE_ROOT/modules" /etc/modules-load.d
TDH_SOURCE_ONLY=1 source "$TDH_TEST_SCRIPT"
check_tuning_support() { :; }
tuning_is_active() { :; }
save_state() { :; }
sysctl() { [[ $1 == -p ]] || printf '%s\n' "$TDH_QUEUE_CURRENT"; }
apply_tuning
'''
                result = subprocess.run(['unshare','--mount','bash','-c',child],env=env,capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                text = (root/'sysctl/90-telemt-dual-hop.conf').read_text()
                for key in ('net.core.somaxconn','net.ipv4.tcp_max_syn_backlog'):
                    self.assertIn(f'{key} = {expected}',text)


if __name__ == "__main__":
    unittest.main()
