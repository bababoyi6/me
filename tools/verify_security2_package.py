"""Verify the fixed security2.2 archive before uploading it as a preview asset."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tarfile


CANDIDATE_SHA256 = '0b901711296707c23da7e8edda5509f108bd351f6e4b9c72f0f69a1b1fe71388'
CANDIDATE_VERSION = 'telemt 3.5.5+tdh.balanced2.security2.2'
UPSTREAM_COMMIT = 'ac71d92ec41dea00a7eafd6b8d350c3486633500'
REQUIRED = {'LICENSE', 'NOTICE.txt', 'manifest.json', 'telemt-bulk',
            'build_relay_candidate.py', 'base.patch',
            'telemt-3.5.5-tls-cache-expiry-wakeup.patch'}


def verify(archive: Path, checksum_file: Path, project_commit: str):
    expected_line = checksum_file.read_text(encoding='ascii').strip()
    match = re.fullmatch(r'([a-f0-9]{64})  ([^/\\\s]+)', expected_line)
    if match is None or match[2] != archive.name:
        raise ValueError('invalid archive checksum record')
    archive_data = archive.read_bytes()
    if not 0 < len(archive_data) <= 64 * 1024**2 or hashlib.sha256(archive_data).hexdigest() != match[1]:
        raise ValueError('archive digest or size mismatch')
    with tarfile.open(archive, 'r:gz') as bundle:
        members = bundle.getmembers()
        names = [item.name for item in members]
        if (len(names) != len(set(names)) or not REQUIRED.issubset(names)
                or any(not item.isfile() or item.name != Path(item.name).name
                       or item.size < 0 or item.size > 64 * 1024**2 for item in members)
                or sum(item.size for item in members) > 128 * 1024**2):
            raise ValueError('unsafe or incomplete archive member list')
        def content(name):
            with bundle.extractfile(name) as stream:
                return stream.read()
        manifest = json.loads(content('manifest.json'))
        pin = manifest['binaries']['bulk']
        binary = content('telemt-bulk')
        if (manifest['project_commit'] != project_commit
                or manifest['source_commit'] != UPSTREAM_COMMIT
                or manifest['candidate_kind'] != 'balanced2-security2'
                or manifest['default_installer_changed'] is not False
                or pin['sha256'] != CANDIDATE_SHA256 or pin['version'] != CANDIDATE_VERSION
                or pin['bytes'] != len(binary)
                or hashlib.sha256(binary).hexdigest() != CANDIDATE_SHA256
                or binary[:6] != b'\x7fELF\x02\x01' or binary[18:20] != b'\x3e\x00'
                or b'Experimental build' not in content('NOTICE.txt')
                or not content('LICENSE').strip()):
            raise ValueError('candidate identity, license or notice mismatch')
    return {'archive_sha256': match[1], 'archive_bytes': len(archive_data),
            'binary_sha256': CANDIDATE_SHA256, 'binary_bytes': len(binary),
            'project_commit': project_commit, 'members': names}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--checksum-file', type=Path, required=True)
    parser.add_argument('--project-commit', required=True)
    print(json.dumps(verify(**vars(parser.parse_args())), sort_keys=True))
