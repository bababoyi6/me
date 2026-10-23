"""Exercise first install, repeated downloaded installs and failures on private files.

No packages, system services or host configuration are changed. Only ownership
options of install(1) are omitted so these filesystem tests also run unprivileged.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "telemt-dual-hop.sh"


class ManagerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tdh-manager-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.manager = self.base / "manager"
        self.shortcut = self.base / "a"
        self.candidate = self.base / "download.sh"
        self.candidate.write_bytes(SCRIPT.read_bytes())
        commands = self.base / "commands"
        commands.mkdir()
        # Execute only this private test manager; never invoke host sudo.
        sudo = commands / "sudo"
        sudo.write_text('#!/bin/bash\n[[ ${1:-} != -- ]] || shift\nexec "$@"\n')
        sudo.chmod(0o755)
        self.env = dict(os.environ, TDH_BASE=str(self.base), TDH_STATE=str(self.base / "state.env"),
                        TDH_MANAGER=str(self.manager), TDH_SHORTCUT=str(self.shortcut),
                        TDH_TEST_SOURCE=str(self.candidate), PATH=str(commands) + os.pathsep + os.environ["PATH"])
        self.protected = {
            "state.env": b"TDH_ROLE=entry\nCONFIG_SCHEMA=5\nPORT_A=443\nPORT_B=443\n",
            "keys/private.key": b"SYNTHETIC_PRIVATE_KEY\n",
            "wireguard.conf": b"AllowedIPs = 10.77.1.2/32\n",
            "telemt.toml": b'line_a = "SYNTHETIC_SECRET"\n',
            "links.txt": b"https://t.me/proxy?server=203.0.113.10&port=443&secret=synthetic\n",
        }
        for name, data in self.protected.items():
            path = self.base / name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(data)

    def invoke(self, command="install_self", prelude="", ok=True):
        setup = '''set -Eeuo pipefail
export TDH_SOURCE_ONLY=1
source "$TDH_TEST_SOURCE"
unset TDH_SOURCE_ONLY
install() {
  local -a args=()
  while (( $# )); do
    case $1 in -o|-g) shift 2 ;; *) args+=("$1"); shift ;; esac
  done
  command install "${args[@]}"
}
systemctl() { printf 'Unexpected service operation\\n' >&2; return 99; }
apt_get_with_lock_wait() { return 99; }
'''
        result = subprocess.run(["bash", "-c", setup + prelude + "\n" + command], env=self.env,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        for name, data in self.protected.items():
            self.assertEqual((self.base / name).read_bytes(), data, name)
        return result

    def old_manager(self, version="1.3.1"):
        data = SCRIPT.read_bytes().replace(b'TDH_VERSION="1.5.0"', f'TDH_VERSION="{version}"'.encode())
        self.manager.write_bytes(data)
        self.manager.chmod(0o755)
        return data

    def test_first_install_creates_working_shortcut(self):
        (self.base / "state.env").unlink()
        self.protected.pop("state.env")
        self.invoke()
        self.assertEqual(self.manager.read_bytes(), SCRIPT.read_bytes())
        self.assertFalse((self.base / "manager.previous").exists())
        result = subprocess.run(["bash", str(self.shortcut), "--help"], env=self.env,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Telemt Dual-Hop 1.5.0", result.stdout)

    def test_repeat_install_and_rollback_preserve_three_role_identities(self):
        for role in ("entry", "backend1", "backend2"):
            with self.subTest(role=role):
                self.protected["state.env"] = f"TDH_ROLE={role}\nCONFIG_SCHEMA=5\nPORT_A=443\nPORT_B=443\n".encode()
                (self.base / "state.env").write_bytes(self.protected["state.env"])
                old = self.old_manager()
                self.invoke()
                self.invoke()
                self.assertEqual((self.base / "manager.previous").read_bytes(), old)
                self.invoke("rollback_manager")
                self.assertEqual(self.manager.read_bytes(), old)

    def test_rejects_downloaded_downgrade_and_protocol_change(self):
        for version, replacement in (("9.9.9", None), ("1.3.1", (b'SCHEMA="5"', b'SCHEMA="6"')),
                                     ("1.3.1", (b'PROTOCOL_VERSION="1.2.1"', b'PROTOCOL_VERSION="9.0.0"'))):
            old = self.old_manager(version)
            if replacement:
                old = old.replace(*replacement)
                self.manager.write_bytes(old)
            self.invoke(ok=False)
            self.assertEqual(self.manager.read_bytes(), old)
            self.assertFalse(self.shortcut.exists())

    def test_selftest_deadline_failure_keeps_existing_manager(self):
        old = self.old_manager()
        self.invoke(prelude='timeout() { [[ $1 == 60 && ${4:-} == --self-test ]]; return 124; }', ok=False)
        self.assertEqual(self.manager.read_bytes(), old)
        self.assertFalse(self.shortcut.exists())

    def test_shortcut_failure_keeps_manager_and_removes_staged_file(self):
        old = self.old_manager()
        previous = self.base / "manager.previous"
        previous.write_bytes(b"SYNTHETIC_EXISTING_ROLLBACK\n")
        self.invoke(prelude="install_shortcut() { return 1; }", ok=False)
        self.assertEqual(self.manager.read_bytes(), old)
        self.assertEqual(previous.read_bytes(), b"SYNTHETIC_EXISTING_ROLLBACK\n")
        self.assertEqual(list(self.base.glob("manager.update.*")), [])

    def test_real_shortcut_permission_failure_is_not_masked(self):
        old = self.old_manager()
        original = b"#!/bin/bash\n# Managed by Telemt Dual-Hop\nexit 0\n"
        self.shortcut.write_bytes(original)
        self.invoke(prelude="chmod() { return 1; }", ok=False)
        self.assertEqual(self.shortcut.read_bytes(), original)
        self.assertEqual(self.manager.read_bytes(), old)
        self.assertEqual(list(self.base.glob("a.update.*")), [])

    def test_failed_final_rename_keeps_old_executable_and_backup(self):
        old = self.old_manager()
        previous = self.base / "manager.previous"
        previous.write_bytes(b"SYNTHETIC_EXISTING_ROLLBACK\n")
        self.invoke(prelude='mv() { [[ ${@: -1} != "$TDH_MANAGER" ]] || return 1; command mv "$@"; }', ok=False)
        self.assertEqual(self.manager.read_bytes(), old)
        self.assertEqual(previous.read_bytes(), b"SYNTHETIC_EXISTING_ROLLBACK\n")
        self.assertEqual(list(self.base.glob("manager.update.*")), [])
        self.assertEqual(list(self.base.glob("manager.restore.*")), [])

    def test_failed_first_replacement_does_not_invent_previous_backup(self):
        old = self.old_manager()
        self.invoke(prelude='mv() { [[ ${@: -1} != "$TDH_MANAGER" ]] || return 1; command mv "$@"; }', ok=False)
        self.assertEqual(self.manager.read_bytes(), old)
        self.assertFalse((self.base / "manager.previous").exists())

    def test_refuses_manager_and_backup_symlinks_without_touching_target(self):
        target = self.base / "unrelated"
        expected = b"SYNTHETIC_UNRELATED_DO_NOT_CHANGE\n"
        target.write_bytes(expected)
        self.manager.symlink_to(target)
        self.invoke(ok=False)
        self.manager.unlink()
        old = self.old_manager()
        (self.base / "manager.previous").symlink_to(target)
        self.invoke(ok=False)
        self.assertEqual(target.read_bytes(), expected)
        self.assertEqual(self.manager.read_bytes(), old)

    def test_legacy_fixed_backup_temporary_path_is_never_used(self):
        old = self.old_manager()
        target = self.base / "unrelated"
        expected = b"SYNTHETIC_UNRELATED_DO_NOT_CHANGE\n"
        target.write_bytes(expected)
        (self.base / "manager.previous.tmp").symlink_to(target)
        self.invoke()
        self.assertEqual(target.read_bytes(), expected)
        self.assertEqual((self.base / "manager.previous").read_bytes(), old)

    def test_rollback_rejects_directory_target_without_copying_inside_it(self):
        previous = self.base / "manager.previous"
        expected = self.old_manager()
        previous.write_bytes(expected)
        self.manager.unlink()
        self.manager.mkdir()
        sentinel = self.manager / "unrelated"
        sentinel.write_bytes(b"SYNTHETIC_KEEP_DIRECTORY_CONTENTS\n")
        self.invoke("rollback_manager", ok=False)
        self.assertEqual(list(self.manager.iterdir()), [sentinel])
        self.assertEqual(previous.read_bytes(), expected)
        self.assertEqual(list(self.base.glob("manager.rollback.*")), [])

    def test_rollback_rejects_symlink_target_without_replacing_it(self):
        previous = self.base / "manager.previous"
        previous.write_bytes(self.old_manager())
        self.manager.unlink()
        target = self.base / "unrelated"
        target.write_bytes(b"SYNTHETIC_KEEP_LINK_TARGET\n")
        self.manager.symlink_to(target)
        self.invoke("rollback_manager", ok=False)
        self.assertTrue(self.manager.is_symlink())
        self.assertEqual(target.read_bytes(), b"SYNTHETIC_KEEP_LINK_TARGET\n")

    def test_rollback_stage_and_rename_failures_preserve_both_versions(self):
        old = self.old_manager("1.4.1")
        previous = self.base / "manager.previous"
        expected = SCRIPT.read_bytes().replace(b'TDH_VERSION="1.5.0"', b'TDH_VERSION="1.3.1"')
        previous.write_bytes(expected)
        for prelude in ("install() { return 74; }", "mv() { return 73; }"):
            with self.subTest(failure=prelude):
                self.invoke("rollback_manager", prelude=prelude, ok=False)
                self.assertEqual(self.manager.read_bytes(), old)
                self.assertEqual(previous.read_bytes(), expected)
                self.assertEqual(list(self.base.glob("manager.rollback.*")), [])


if __name__ == "__main__":
    unittest.main()
