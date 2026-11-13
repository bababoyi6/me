"""Bounded synthetic protocol checks for both TLS candidates and released balanced2."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile

from e2e_lab import FullLab
from lab_files import file_sha256
from mtproto_transport import faketls_handshake
from paired_performance import resources, write
from release_pins import TLS_BASELINE, TLS_CANDIDATES
from telemt_probe import SECRETS, request
from core_install import archive_payload


def assert_authentication_rejected(lab):
    with socket.create_connection(('127.0.0.1', lab.front), timeout=3) as client:
        try:
            faketls_handshake(client, 'apple.com', bytes(16))
        except (EOFError, OSError, ValueError):
            return
    raise RuntimeError('unexpected acceptance of an invalid synthetic secret')


def candidate_binary(directory, candidate, source_commit):
    kind, source_field, source, version = TLS_CANDIDATES[candidate]
    manifest = json.loads((directory / 'manifest.json').read_text())
    project_field = 'project_commit' if candidate == 'old' else 'manager_commit'
    pin = manifest['binaries'][kind]
    binary = directory / ('telemt-' + kind)
    if (manifest[source_field] != source or manifest[project_field] != source_commit or
            pin['version'] != version or not 0 < binary.stat().st_size <= 64 * 1024**2 or
            file_sha256(binary) != pin['sha256']):
        raise RuntimeError('candidate manifest or binary mismatch')
    with binary.open('rb') as stream:
        elf = stream.read(20)
    if elf[:6] != b'\x7fELF\x02\x01' or elf[18:20] != b'\x3e\x00':
        raise RuntimeError('expected x86_64 Linux ELF')
    return binary, pin


def main(args):
    before = resources()
    if (os.geteuid() != 0 or os.environ.get('GITHUB_ACTIONS') != 'true' or
            os.readlink('/proc/self/ns/net') == os.readlink('/proc/1/ns/net') or
            before['cpu_max'] != '100000 100000' or before['memory_max'] != str(512 * 1024**2) or
            before['swap_max'] != '0'):
        raise RuntimeError('owned bounded CI namespace required')
    links = subprocess.check_output(['ip', '-j', 'link', 'show'], text=True, timeout=5)
    if [entry['ifname'] for entry in json.loads(links)] != ['lo']:
        raise RuntimeError('loopback-only namespace required')
    report = {'source_commit': os.environ['TDH_SOURCE_COMMIT'], 'rows': [],
              'resources_before': before, 'accepted': False}
    try:
        with tempfile.TemporaryDirectory(prefix='tdh-tls-candidate-') as name:
            root = Path(name)
            baseline = root / 'balanced2'
            payload, _ = archive_payload(args.balanced2_archive, TLS_BASELINE[0],
                                         TLS_BASELINE[2].removeprefix('telemt '), TLS_BASELINE[1], False)
            baseline.write_bytes(payload)
            cases = [('released', baseline, TLS_BASELINE[1], TLS_BASELINE[2])]
            binary, pin = candidate_binary(args.candidate, args.kind, report['source_commit'])
            cases.append((args.kind, binary, pin['sha256'], pin['version']))
            for label, binary, digest, version in cases:
                with binary.open('rb') as stream:
                    elf = stream.read(20)
                if elf[:6] != b'\x7fELF\x02\x01' or elf[18:20] != b'\x3e\x00':
                    raise RuntimeError('expected x86_64 Linux ELF')
                row = {'kind': label, 'version': version, 'sha256': digest, 'transfers': []}
                report['rows'].append(row)
                lab = FullLab(binary, offline_lab=True, synthetic_tls_front=True,
                              experimental_binary=(digest, version))
                try:
                    row['api_unauthorized'] = request(lab.api, 'GET', '/v1/users', False)[0]
                    row['api_read_only'] = request(lab.api, 'POST', '/v1/users')[0]
                    if (row['api_unauthorized'], row['api_read_only']) != (401, 403):
                        raise RuntimeError('API boundary regression')
                    assert_authentication_rejected(lab)
                    row['invalid_auth_rejected'] = True
                    for domain in SECRETS:
                        for direction in ('D', 'U'):
                            row['transfers'].append(lab.transfer(domain, direction, 131072, 2500000,
                                                                 fragment=True, persistent=True))
                    lab.validate(len(row['transfers']))
                    row['configuration_sha256'] = hashlib.sha256(lab.telemt_config.read_bytes()).hexdigest()
                finally:
                    lab.close()
                    row['processes_closed'] = all(process.poll() is not None for process in lab.processes)
        report['accepted'] = all(row['processes_closed'] and
                                 all(flow['sha256_verified'] for flow in row['transfers'])
                                 for row in report['rows'])
    finally:
        after = resources()
        report['resources_after'] = after
        report['oom_delta'] = {key: after['memory_events'][key] - before['memory_events'][key]
                               for key in ('oom', 'oom_kill')}
        report['accepted'] = report['accepted'] and not any(report['oom_delta'].values())
        write(args.output, report)
    return 0 if report['accepted'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', required=True, choices=TLS_CANDIDATES)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--balanced2-archive', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    raise SystemExit(main(parser.parse_args()))
