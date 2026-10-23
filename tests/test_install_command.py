"""Run the exact README command against a local download boundary, without sudo."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class InstallCommandTests(unittest.TestCase):
    def invoke(self, download_ok, root=True, payload=None):
        command = (ROOT / "README.md").read_text().split("```bash\n", 1)[1].split("\n```", 1)[0]
        with tempfile.TemporaryDirectory(prefix="tdh-bootstrap-test-") as name:
            base = Path(name)
            commands = base / "commands"
            commands.mkdir()
            temporary = base / "temporary"
            temporary.mkdir()
            existing = temporary / "telemt-dual-hop.sh"
            existing.write_bytes(b"SYNTHETIC_UNRELATED_EXISTING_FILE\n")
            candidate = ROOT / "telemt-dual-hop.sh"
            if payload is not None:
                candidate = base / "payload.sh"
                candidate.write_bytes(payload)
            expected_success = download_ok and payload is None
            scripts = {
                "curl": '''#!/bin/bash
set -eu
printf '%s\\n' "$@" >"$TDH_BOOTSTRAP_TEST/curl-arguments"
[[ $TDH_TEST_DOWNLOAD_OK == 1 ]] || exit 22
cp "$TDH_TEST_PAYLOAD" "${@: -1}"
''',
                "id": '#!/bin/bash\nprintf "%s\\n" "$TDH_TEST_UID"\n',
                "sudo": '''#!/bin/bash
set -eu
[[ $TDH_TEST_UID != 0 && $1 == bash && -f $2 && $# == 2 ]]
exec "$@"
''',
                "bash": '''#!/bin/bash
set -eu
if [[ ${1:-} == "$TMPDIR/"* && -f $1 ]]; then
printf 'executed\\n' >"$TDH_BOOTSTRAP_TEST/execution"
# Exercise the downloaded script's real help path; no install or menu on host.
exec /bin/bash "$@" --help
fi
exec /bin/bash "$@"
''',
            }
            for executable, body in scripts.items():
                path = commands / executable
                path.write_text(body)
                path.chmod(0o755)
            env = dict(os.environ, PATH=str(commands) + os.pathsep + os.environ["PATH"], TMPDIR=str(temporary),
                       TDH_BOOTSTRAP_TEST=str(base), TDH_TEST_DOWNLOAD_OK=str(int(download_ok)),
                       TDH_TEST_UID="0" if root else "1000", TDH_TEST_PAYLOAD=str(candidate))
            result = subprocess.run(["bash", "-c", command], env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode == 0, expected_success, result.stdout + result.stderr)
            self.assertEqual((base / "execution").exists(), expected_success)
            self.assertEqual(list(temporary.iterdir()), [existing])
            self.assertEqual(existing.read_bytes(), b"SYNTHETIC_UNRELATED_EXISTING_FILE\n")
            arguments = (base / "curl-arguments").read_text().splitlines()
            self.assertIn("--max-time", arguments)
            self.assertEqual(arguments[0], "-q")
            self.assertIn("--max-filesize", arguments)
            self.assertIn("--proto", arguments)
            self.assertIn("=https", arguments)
            if expected_success:
                self.assertIn("Telemt Dual-Hop 1.5.0", result.stdout)

    def test_download_and_launch_then_clean_private_temporary_file(self):
        self.invoke(True)

    def test_nonroot_install_uses_sudo_boundary(self):
        self.invoke(True, root=False)

    def test_download_failure_never_launches_or_leaves_partial_file(self):
        self.invoke(False)

    def test_empty_wrong_project_and_invalid_shell_never_launch(self):
        for payload in (b'', b'#!/bin/bash\necho MUST_NOT_EXECUTE\n',
                        b'# Telemt Dual-Hop - three VPS / two native MTProxy links\nif\n'):
            with self.subTest(payload_bytes=len(payload)):
                self.invoke(True,payload=payload)


if __name__ == "__main__":
    unittest.main()
