"""Run the immutable security2.2 core in a dedicated private network screen."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from network_lab import RESERVED_NAMES, command
from paired_performance import resources
from release_pins import OLD_TLS_CACHE_SHA256, TLS_CANDIDATES

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'tools'))
from core_install import archive_payload


ARCHIVE_SHA256 = '38b6bf95c6fd02c6ee3f160aac11163808d7f80ecc0c0702a08edc37c63aecbb'


def remaining_namespaces():
    return [row.split()[0] for row in command('ip', 'netns', 'list').splitlines()
            if row.split()[0] in RESERVED_NAMES]


def temporary_artifacts():
    return {p.name for pattern in ('tdh-audit-dual-*', 'tdh-e2e-*') for p in Path('/tmp').glob(pattern)}


def main(args):
    if os.geteuid() != 0 or os.environ.get('GITHUB_ACTIONS') != 'true':
        raise RuntimeError('disposable GitHub CI runner required')
    runner_temp = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    if (not args.evidence.is_dir() or not args.evidence.resolve().is_relative_to(runner_temp)
            or any(args.evidence.iterdir()) or not args.archive.resolve().is_relative_to(runner_temp)):
        raise RuntimeError('fresh evidence directory and fixed archive within runner temp required')
    before = resources()
    if (before['cpu_max'] != '300000 100000' or before['memory_max'] != str(1536 * 1024 ** 2)
            or before['swap_max'] != '0' or before['pids_current'] != 1 or remaining_namespaces()):
        raise RuntimeError('private resource limits or initial namespace check failed')
    version = TLS_CANDIDATES['oldcache'][3]
    payload, _ = archive_payload(args.archive, ARCHIVE_SHA256, version.removeprefix('telemt '),
                                 OLD_TLS_CACHE_SHA256, audited=True)
    before_artifacts = temporary_artifacts()
    report = {'source_commit': os.environ['TDH_SOURCE_COMMIT'], 'archive_sha256': ARCHIVE_SHA256,
              'binary_sha256': OLD_TLS_CACHE_SHA256, 'version': version, 'resources_before': before,
              'worker_exit': None, 'complete': False, 'qualified': False, 'cleanup_verified': False}
    try:
        with tempfile.TemporaryDirectory(prefix='tdh-audit-shared-queue-', dir=runner_temp) as directory:
            root = Path(directory)
            target = root / 'telemt-bulk'
            target.write_bytes(payload)
            target.chmod(0o755)
            del payload
            (root / 'bulk-build.json').write_text(json.dumps({'sha256': OLD_TLS_CACHE_SHA256, 'version': version}))
            try:
                worker = subprocess.run(['timeout', '--signal=TERM', '--kill-after=10', '280',
                    sys.executable, '-B', str(REPO / 'tests/dualhop_lab.py'), '--root', str(root),
                    '--build', 'bulk', '--separate-dc', '--rtt', '100', '--shared-queue', 'run'], timeout=295)
                report['worker_exit'] = worker.returncode
            finally:
                for name in ('dual-shared-calibration.json', 'dual-shared-queue.json'):
                    path = root / 'results' / name
                    if path.is_file():
                        (args.evidence / name).write_bytes(path.read_bytes())
                report['remaining_key_files'] = sorted(path.name for path in root.glob('*.key'))
        result_path = args.evidence / 'dual-shared-queue.json'
        result = json.loads(result_path.read_text()) if result_path.is_file() else {}
        report['complete'] = report['worker_exit'] == 0 and result.get('complete') is True
        report['qualified'] = report['complete'] and result.get('qualified') is True
    finally:
        after = resources()
        report['resources_after'] = after
        report['oom_delta'] = {key: after['memory_events'][key] - before['memory_events'][key]
                               for key in ('oom', 'oom_kill')}
        report['remaining_namespaces'] = remaining_namespaces()
        report['remaining_temporary_artifacts'] = sorted(temporary_artifacts() - before_artifacts)
        report['cleanup_verified'] = (not report['remaining_namespaces'] and not report.get('remaining_key_files')
                                      and not report['remaining_temporary_artifacts'] and after['pids_current'] == 1)
        report['complete'] &= report['cleanup_verified'] and not any(report['oom_delta'].values())
        report['qualified'] &= report['complete']
        (args.evidence / 'supervisor.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    return 0 if report['complete'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True, type=Path)
    parser.add_argument('--evidence', required=True, type=Path)
    raise SystemExit(main(parser.parse_args()))
