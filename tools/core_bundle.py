"""Build a private, deterministic bundle of the exact tested x86_64 core.

Offline packaging only: no download, execution, installation or publication.
The resulting archive is experimental and is not an enabled updater channel.
"""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CORE_SHA256 = '2e5f864461be842dbca2ee8638f3b68327f56d5f1666ea0f2622a491b2ba75d1'
PATCH_SHA256 = '3daeee2ebec742d259372d45480647029bc9437f700ecaa8b94e0124af74b734'
VERSION = '3.5.5+tdh.audit2'
SOURCE_COMMIT = 'ac71d92ec41dea00a7eafd6b8d350c3486633500'
NOTICE = b'''Unofficial modified Telemt build: 3.5.5+tdh.audit2.
Based on Telemt 3.5.5, commit ac71d92ec41dea00a7eafd6b8d350c3486633500.
Source: https://github.com/telemt/telemt
Modifications: progress buffered relay output when input is pending; retain
Linux TCP autotuning for client and Direct outgoing sockets; mark version.
No endorsement by Telemt maintainers. Not an official Telemt release.
Original Telemt copyright and accompanying TELEMT-LICENSE.txt are preserved.
Experimental x86_64 GNU/Linux artifact; no ARM compatibility claim.
No production throughput or long-term stability guarantee is implied.
'''


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_regular(path, limit):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
        raise ValueError('bounded regular input file required')
    data = path.read_bytes()
    if len(data) != info.st_size:
        raise ValueError('input changed during packaging')
    return data


def write_bundle(output, files, manifest):
    """Serialize an audited payload deterministically and publish it exclusively."""
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError('refusing to overwrite existing bundle')
    files = dict(files)
    files['manifest.json'] = (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()
    stage = None
    try:
        descriptor, stage = tempfile.mkstemp(prefix=output.name + '.stage-', dir=output.parent)
        with os.fdopen(descriptor, 'wb') as target:
            with gzip.GzipFile(filename='', fileobj=target, mode='wb', mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode='w', format=tarfile.USTAR_FORMAT) as archive:
                    for name, content in sorted(files.items()):
                        info = tarfile.TarInfo(name)
                        info.size, info.mode, info.mtime = len(content), 0o755 if name == 'telemt' else 0o644, 0
                        archive.addfile(info, io.BytesIO(content))
            target.flush()
            os.fsync(target.fileno())
        # Exclusive publication into the local directory; never clobber a race.
        os.link(stage, output)
    finally:
        if stage is not None:
            Path(stage).unlink(missing_ok=True)
    return {'bundle_sha256': sha256(output.read_bytes()), 'files': len(files)}


def bundle(binary, output):
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError('refusing to overwrite existing bundle')
    data = read_regular(binary, 64 * 1024**2)
    if sha256(data) != CORE_SHA256:
        raise ValueError('binary is not the exact validated candidate')
    # ELF64 little endian / EM_X86_64. Never execute a packager input as root.
    if len(data) < 64 or data[:6] != b'\x7fELF\x02\x01' or data[18:20] != b'\x3e\x00':
        raise ValueError('validated x86_64 ELF required')
    patch = read_regular(ROOT/'patches/telemt-3.5.5-relay-backpressure.patch', 128 * 1024)
    if sha256(patch) != PATCH_SHA256:
        raise ValueError('source patch differs from the validated build')
    provenance = read_regular(ROOT/'docs/benchmarks/native-build-manifest.json', 64 * 1024)
    build = json.loads(provenance)
    if (build['source_commit'] != SOURCE_COMMIT or build['patch_sha256'] != PATCH_SHA256
            or build['builds']['auto'] != {'sha256': CORE_SHA256, 'version': 'telemt ' + VERSION}):
        raise ValueError('build provenance mismatch')
    files = {'telemt': data, 'TELEMT-LICENSE.txt': read_regular(ROOT/'patches/TELEMT-LICENSE.txt', 128 * 1024),
             'NOTICE.txt': NOTICE, 'source.patch': patch, 'build-provenance.json': provenance}
    manifest = {'schema': 1, 'version': VERSION, 'architecture': 'x86_64', 'os': 'linux',
                'libc': 'gnu', 'minimum_glibc': '2.35', 'source_commit': SOURCE_COMMIT,
                'unofficial': True, 'channel': 'private-experimental', 'updater_enabled': False,
                'files': {name: {'sha256': sha256(content), 'bytes': len(content),
                                 'mode': '0755' if name == 'telemt' else '0644'}
                          for name, content in files.items()}}
    result = write_bundle(output, files, manifest)
    return dict(result, binary_sha256=CORE_SHA256, version=VERSION, published=False, updater_enabled=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(bundle(args.binary, args.output), sort_keys=True))
