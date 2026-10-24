"""Package only the pinned balanced2 CI artifact; never execute, install or publish it."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile

from core_bundle import read_regular, sha256, write_bundle

ROOT = Path(__file__).resolve().parents[1]
PAIR_SHA256 = 'bdb602274776fc6b29a5b2259a8d17bc2d3f65bcf974828c429c861cfb639dcb'
CORE_SHA256 = '5dc5d9b5ac43a0f12a402159e127b3b69e18ccb355c99b0322e73d870d907100'
FLUSH_SHA256 = '35b23d84ab1e6394e0ec4d36c215ff9295d1cd8828e214c9d79dd55577f17ef9'
VERSION = '3.5.5+tdh.balanced2'
SOURCE_COMMIT = 'ac71d92ec41dea00a7eafd6b8d350c3486633500'
BUILD_COMMIT = '02ec71320c831735b025107f5b2181097657ac96'
SOURCES = ('base.patch', 'build_relay_candidate.py', 'fair_copy.rs', 'unsent_queue.rs',
           'unsent_queue_bulk.rs', 'low_rtt_receive.rs')
MEMBERS = {'LICENSE', 'NOTICE.txt', 'manifest.json', *SOURCES,
           'telemt-fixed', 'telemt-control', 'telemt-balanced', 'telemt-bulk'}
NOTICE = b'''Unofficial experimental Telemt 3.5.5+tdh.balanced2, x86_64 GNU/Linux.
Based on Telemt commit ac71d92ec41dea00a7eafd6b8d350c3486633500.
Source: https://github.com/telemt/telemt
Build source: https://github.com/bababoyi6/me/tree/02ec71320c831735b025107f5b2181097657ac96
Modifications: buffered relay flush; automatic TCP buffers; receive-buffer request
on measured short-RTT client hops; outgoing unsent low-water threshold256KiB.
source.patch is the base audit2 patch. Additional source helpers and the exact
builder are preserved, with hashes, in build-provenance.json/source_kit.
Original Telemt copyright and TELEMT-LICENSE.txt are preserved.
No endorsement by Telemt maintainers. Not an official Telemt release.
No production, long-term stability or ARM compatibility claim. Updater disabled.
'''


def bundle(pair, output):
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError('refusing to overwrite existing bundle')
    data = read_regular(pair, 64*1024**2)
    if sha256(data) != PAIR_SHA256:
        raise ValueError('not the pinned native candidate archive')
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        entries = archive.getmembers()
        if (len(entries) != len(MEMBERS) or {p.name for p in entries} != MEMBERS or
                any(not p.isfile() or not 0 < p.size <= 64*1024**2 for p in entries) or
                sum(p.size for p in entries) > 192*1024**2):
            raise ValueError('candidate archive members mismatch')
        native = json.load(archive.extractfile('manifest.json'))
        if (set(native['binaries']) != {'fixed','control','balanced','bulk'} or
                native['source_commit'] != SOURCE_COMMIT or native['project_commit'] != BUILD_COMMIT or
                native['candidate_kind'] != 'bulk' or native['outgoing_unsent_low_water_bytes'] != 262144 or
                native['binaries']['bulk']['version'] != 'telemt '+VERSION or
                native['binaries']['bulk']['sha256'] != CORE_SHA256):
            raise ValueError('candidate provenance mismatch')
        for name, identity in native['binaries'].items():
            digest, size = hashlib.sha256(), 0
            with archive.extractfile('telemt-'+name) as binary:
                for block in iter(lambda: binary.read(65536), b''):
                    digest.update(block)
                    size += len(block)
            if digest.hexdigest() != identity['sha256'] or size != identity['bytes']:
                raise ValueError('native binary identity mismatch')
        sources = {name: archive.extractfile(name).read() for name in SOURCES}
        expected = {'base.patch': native['base_patch_sha256'],
                    'build_relay_candidate.py': native['builder_sha256'],
                    'unsent_queue_bulk.rs': native['outgoing_queue_helper_sha256'],
                    'low_rtt_receive.rs': native['receive_helper_sha256']}
        if any(sha256(sources[name]) != value for name, value in expected.items()):
            raise ValueError('candidate source identity mismatch')
        binary = archive.extractfile('telemt-bulk').read()
        license_text = archive.extractfile('LICENSE').read()
    if len(binary) < 64 or binary[:6] != b'\x7fELF\x02\x01' or binary[18:20] != b'\x3e\x00':
        raise ValueError('x86_64 ELF required')
    flush = read_regular(ROOT/'patches/flushed_copy.rs', 64*1024)
    if sha256(flush) != FLUSH_SHA256:
        raise ValueError('flush replay source mismatch')
    sources['flushed_copy.rs'] = flush
    provenance = {'native_build': native, 'native_archive_sha256': PAIR_SHA256,
                  'source_kit': {name: {'sha256': sha256(content), 'text_utf8': content.decode('utf-8')}
                                 for name, content in sources.items()},
                  'replay': 'Checkout project BUILD_COMMIT and upstream SOURCE_COMMIT; use the pinned builder with --candidate bulk. No reference archive is required.',
                  'production_acceptance': False, 'published': False}
    files = {'telemt': binary, 'TELEMT-LICENSE.txt': license_text, 'NOTICE.txt': NOTICE,
             'source.patch': sources['base.patch'],
             'build-provenance.json': (json.dumps(provenance, sort_keys=True, indent=2)+'\n').encode()}
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
    parser.add_argument('--pair-archive', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(bundle(args.pair_archive, args.output), sort_keys=True))
