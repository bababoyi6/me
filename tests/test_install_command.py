"""Run the exact README command against a local download boundary, without sudo."""
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class InstallCommandTests(unittest.TestCase):
    def invoke(self, download_ok, root=True, payload=None, original=False, public=False):
        guide = ROOT / ("legacy/README.md" if original else "README.md")
        command = guide.read_text(encoding="utf-8").split("```bash\n",1)[1].split("\n```", 1)[0]
        if original:
            channel = "v1.3.1-original"
            version = "1.3.1"
        else:
            channel = re.search(r'/me/(v[0-9]+\.[0-9]+\.[0-9]+)/telemt-dual-hop\.sh', command)[1]
            version = channel.removeprefix("v")
        with tempfile.TemporaryDirectory(prefix="tdh-bootstrap-test-") as name:
            base = Path(name)
            commands = base / "commands"
            commands.mkdir()
            temporary = base / "temporary"
            temporary.mkdir()
            existing = temporary / "telemt-dual-hop.sh"
            existing.write_bytes(b"SYNTHETIC_UNRELATED_EXISTING_FILE\n")
            candidate = ROOT / ("legacy/telemt-dual-hop-1.3.1.sh" if original else "telemt-dual-hop.sh")
            if not original and not public:
                # The working manager and the README's frozen release can differ.
                # Pin the working fixture for local tests; --public uses the exact README.
                command, replacements = re.subn(r'"[a-f0-9]{64}"',
                    f'"{hashlib.sha256(candidate.read_bytes()).hexdigest()}"', command)
                self.assertEqual(replacements, 1)
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
            if public:
                scripts['curl']='#!/bin/bash\nset -eu\nprintf "%s\\n" "$@" >"$TDH_BOOTSTRAP_TEST/curl-arguments"\nexec /usr/bin/curl "$@"\n'
            for executable, body in scripts.items():
                path = commands / executable
                path.write_text(body)
                path.chmod(0o755)
            env = dict(os.environ, PATH=str(commands) + os.pathsep + os.environ["PATH"], TMPDIR=str(temporary),
                       TDH_BOOTSTRAP_TEST=str(base), TDH_TEST_DOWNLOAD_OK=str(int(download_ok)),
                       TDH_TEST_UID="0" if root else "1000", TDH_TEST_PAYLOAD=str(candidate))
            result = subprocess.run(["bash", "-c", command], env=env, capture_output=True, text=True, timeout=150 if public else 10)
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
            self.assertIn(f'https://raw.githubusercontent.com/bababoyi6/me/{channel}/telemt-dual-hop.sh', arguments)
            if expected_success:
                self.assertIn("Telemt Dual-Hop " + version, result.stdout)

    def test_download_and_launch_then_clean_private_temporary_file(self):
        self.invoke(True)

    def test_nonroot_install_uses_sudo_boundary(self):
        self.invoke(True, root=False)

    def test_original_uses_frozen_channel_for_root_and_nonroot(self):
        for root in (True,False):
            with self.subTest(root=root):self.invoke(True,root=root,original=True)

    def test_original_rejects_valid_shell_with_wrong_pinned_digest(self):
        payload=(ROOT/'legacy/telemt-dual-hop-1.3.1.sh').read_bytes()+b'\n# changed after download\n'
        self.invoke(True,payload=payload,original=True)

    def test_stable_rejects_valid_shell_with_wrong_pinned_digest(self):
        self.invoke(True,payload=(ROOT/'telemt-dual-hop.sh').read_bytes()+b'\n# altered\n')

    def test_download_failure_never_launches_or_leaves_partial_file(self):
        self.invoke(False)

    def test_empty_wrong_project_and_invalid_shell_never_launch(self):
        for payload in (b'', b'#!/bin/bash\necho MUST_NOT_EXECUTE\n',
                        b'# Telemt Dual-Hop - three VPS / two native MTProxy links\nif\n'):
            with self.subTest(payload_bytes=len(payload)):
                self.invoke(True,payload=payload)


if __name__ == "__main__":
    import sys
    if sys.argv[1:]==['--public']:
        if os.environ.get('GITHUB_ACTIONS')!='true':
            raise SystemExit('public bootstrap smoke is confined to disposable CI')
        InstallCommandTests().invoke(True,root=False,public=True)
        print('Public README command: HTTPS download, pinned SHA256, real --help and temporary cleanup PASS')
    else:
        unittest.main()
