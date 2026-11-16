"""Convert the fixed security2.2 source package into the manager's audited format.

This is offline packaging. It never executes the Linux binary or overwrites an
existing asset. The original package remains the complete source provenance.
"""
import argparse
import json
from pathlib import Path
import tarfile

from core_bundle import write_bundle, sha256
from verify_security2_package import verify, CANDIDATE_SHA256, CANDIDATE_VERSION


VERSION = CANDIDATE_VERSION.removeprefix('telemt ')
PROJECT_COMMIT = '949476abec8f811ec134fe17342b81355cb32c42'
NOTICE = ("Unofficial modified Telemt build: " + VERSION + ".\n"
          "Based on Telemt 3.5.5; no endorsement by Telemt maintainers.\n"
          "This archive repackages the exact published security2.2 binary for the\n"
          "Telemt Dual-Hop manager. See build-provenance.json and the original\n"
          "security2.2 release archive for complete source and build material.\n"
          "Real Telegram client and production public-network behavior are unverified.\n").encode()


def bundle(source: Path, checksums: Path, output: Path):
    source, checksums, output = map(Path, (source, checksums, output))
    if output.exists() or output.is_symlink():
        raise ValueError('refusing to overwrite an existing install bundle')
    record = verify(source, checksums, PROJECT_COMMIT)
    with tarfile.open(source, 'r:gz') as original:
        def member(name):
            with original.extractfile(name) as stream:
                return stream.read()
        old_manifest = json.loads(member('manifest.json'))
        binary = member('telemt-bulk')
        license_text = member('LICENSE')
        patch_names = sorted(name for name in record['members'] if name.endswith('.patch'))
        patch = b''.join((b'# Original package member: ' + name.encode() + b'\n'
                          + member(name).rstrip(b'\n') + b'\n\n') for name in patch_names)
    provenance = (json.dumps({'schema': 1, 'original_release_archive_sha256': record['archive_sha256'],
                              'original_project_commit': PROJECT_COMMIT,
                              'original_manifest': old_manifest,
                              'source_patch_members': patch_names}, sort_keys=True, indent=2) + '\n').encode()
    files = {'telemt': binary, 'TELEMT-LICENSE.txt': license_text, 'NOTICE.txt': NOTICE,
             'source.patch': patch, 'build-provenance.json': provenance}
    manifest = {'schema': 1, 'version': VERSION, 'architecture': 'x86_64', 'os': 'linux',
                'libc': 'gnu', 'minimum_glibc': '2.35', 'unofficial': True,
                'source_commit': old_manifest['source_commit'],
                'original_release_archive_sha256': record['archive_sha256'],
                'files': {name: {'sha256': sha256(data), 'bytes': len(data),
                                 'mode': '0755' if name == 'telemt' else '0644'}
                          for name, data in files.items()}}
    result = write_bundle(output, files, manifest)
    return {'archive_sha256': result['bundle_sha256'], 'binary_sha256': CANDIDATE_SHA256,
            'version': VERSION, 'original_archive_sha256': record['archive_sha256']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--checksums', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    print(json.dumps(bundle(**vars(parser.parse_args())), sort_keys=True))
