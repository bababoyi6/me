"""Compare fixed security2.2 and released balanced2 on two private tunnels."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'tools'))

from core_install import archive_payload
from release_pins import OLD_TLS_CACHE_SHA256, TLS_BASELINE, TLS_CANDIDATES
from tls_candidate_lab import candidate_binary


ORDER = ('balanced2', 'security2', 'security2', 'balanced2')


def main(args):
    runner_temp = Path(os.environ.get('RUNNER_TEMP', '/nonexistent')).resolve()
    if (os.geteuid() != 0 or os.environ.get('GITHUB_ACTIONS') != 'true'
            or not runner_temp.is_dir() or not args.output.resolve().is_relative_to(runner_temp)):
        raise RuntimeError('ephemeral CI runner and private output required')
    candidate, pin = candidate_binary(args.candidate, 'oldcache', os.environ['GITHUB_SHA'])
    if pin['sha256'] != OLD_TLS_CACHE_SHA256 or pin['version'] != TLS_CANDIDATES['oldcache'][3]:
        raise RuntimeError('fixed security2.2 candidate required')
    archive_sha, binary_sha, version = TLS_BASELINE
    baseline, _ = archive_payload(args.balanced2, archive_sha, version.removeprefix('telemt '), binary_sha, False)
    binaries = {'balanced2': (baseline, binary_sha, version),
                'security2': (candidate.read_bytes(), pin['sha256'], pin['version'])}
    report = {'scope': 'ABBA synthetic 100ms shared 100Mbps entry link; two real private WireGuard tunnels and two backends per run',
              'source_commit': os.environ['GITHUB_SHA'], 'candidate_sha256': pin['sha256'],
              'baseline_sha256': binary_sha, 'order': ORDER, 'runs': [], 'accepted': False}
    try:
        for number, kind in enumerate(ORDER, 1):
            root = runner_temp / f'tdh-audit-security2-capacity-{number}'
            if root.exists() or root.is_symlink():
                raise RuntimeError('capacity root must be fresh')
            root.mkdir(mode=0o700)
            (root / 'results').mkdir(mode=0o700)
            payload, expected_sha, expected_version = binaries[kind]
            target = root / 'telemt-bulk'
            target.write_bytes(payload)
            target.chmod(0o755)
            if hashlib.sha256(target.read_bytes()).hexdigest() != expected_sha:
                raise RuntimeError('staged core hash mismatch')
            (root / 'bulk-build.json').write_text(json.dumps({'sha256': expected_sha,
                                                              'version': expected_version}) + '\n')
            subprocess.run([sys.executable, '-B', str(REPO / 'tools/lab_runner.py'),
                            '--root', str(root), '--profile', 'candidate-capacity',
                            '--rtt', '100', '--memory-high-percent', '80', '--execute'],
                           check=True, timeout=240)
            result = json.loads((root / 'results/candidate-dual-capacity.json').read_text())
            rows = result['rows']
            if (result['telemt'] != expected_version or result['rtt_ms_configured'] != 100
                    or result['shared_entry_link_mbps'] != 100 or result['backend_errors'] != [[], []]
                    or len(rows) != 8 or any(row['sha256_verified'] is not True for row in rows)
                    or [row['bytes'] for row in rows] != [131072] * 4 + [8 * 1024**2] * 4
                    or [(row['direction'], row['backend_index']) for row in rows[4:]]
                    != [('D', 1), ('D', 2), ('U', 1), ('U', 2)]):
                raise RuntimeError('private capacity identity, routing or integrity mismatch')
            rates = {row['direction']: row['aggregate_mbps'] for row in result['capacity']}
            if len(result['capacity']) != 2 or set(rates) != {'D', 'U'} or any(rate <= 0 for rate in rates.values()):
                raise RuntimeError('both capacity directions required')
            report['runs'].append({'kind': kind, 'version': expected_version,
                                   'rates_mbps': rates, 'payload_bytes': result['payload_bytes'],
                                   'wireguard_transfer_delta_bytes': result['wireguard_transfer_delta_bytes'],
                                   'sha256_verified': True})
            args.output.write_text(json.dumps(report, indent=2) + '\n')
        ratios = {direction: statistics.median(row['rates_mbps'][direction]
                     for row in report['runs'] if row['kind'] == 'security2') /
                     statistics.median(row['rates_mbps'][direction]
                     for row in report['runs'] if row['kind'] == 'balanced2')
                  for direction in ('D', 'U')}
        report['candidate_to_baseline_ratio'] = ratios
        report['accepted'] = all(ratio >= .90 for ratio in ratios.values())
    finally:
        args.output.write_text(json.dumps(report, indent=2) + '\n')
    return 0 if report['accepted'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--balanced2', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    raise SystemExit(main(parser.parse_args()))
