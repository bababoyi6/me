"""Stage the fixed security2.2 binary for a bounded private two-tunnel check."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys

from release_pins import OLD_TLS_CACHE_SHA256, TLS_CANDIDATES
from tls_candidate_lab import candidate_binary


REPO = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True, type=Path)
    args = parser.parse_args()
    runner_temp = Path(os.environ.get('RUNNER_TEMP', '/nonexistent')).resolve()
    root = runner_temp / 'tdh-audit-security2-dualpath'
    if (os.geteuid() != 0 or os.environ.get('GITHUB_ACTIONS') != 'true'
            or root.exists() or root.is_symlink() or not runner_temp.is_dir()):
        parser.error('fresh root in an ephemeral CI runner required')
    binary, pin = candidate_binary(args.candidate, 'oldcache', os.environ['GITHUB_SHA'])
    if pin['sha256'] != OLD_TLS_CACHE_SHA256 or pin['version'] != TLS_CANDIDATES['oldcache'][3]:
        parser.error('fixed security2.2 candidate required')
    os.umask(0o077)
    root.mkdir(mode=0o700)
    (root / 'results').mkdir(mode=0o700)
    target = root / 'telemt-bulk'
    shutil.copyfile(binary, target)
    target.chmod(0o755)
    (root / 'bulk-build.json').write_text(json.dumps(pin, sort_keys=True) + '\n')
    argv = [sys.executable, '-B', str(REPO / 'tools/lab_runner.py'), '--root', str(root),
            '--profile', 'candidate', '--rtt', '100', '--memory-high-percent', '80', '--execute']
    os.execv(sys.executable, argv)


if __name__ == '__main__':
    raise SystemExit(main())
