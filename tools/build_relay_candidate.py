#!/usr/bin/env python3
"""Build same-toolchain control/candidate binaries in CI; never install a service."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

SOURCE_COMMIT = 'ac71d92ec41dea00a7eafd6b8d350c3486633500'
ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(65536), b''):
            value.update(block)
    return value.hexdigest()


def run(*argv, cwd, timeout=120):
    return subprocess.run(argv, cwd=cwd, check=True, text=True, timeout=timeout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--candidate', choices=('fair', 'queue', 'balanced'), default='fair')
    parser.add_argument('--reference-archive')
    args = parser.parse_args()
    source = Path(args.source).resolve()
    output = Path(args.output).resolve()
    assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip() == SOURCE_COMMIT
    assert not subprocess.check_output(['git', 'status', '--porcelain'], cwd=source)
    output.mkdir(mode=0o700)
    rust = subprocess.check_output(['rustc', '-V'], text=True).strip()
    assert rust.split()[1] == '1.99.1', rust
    os.environ.update(CARGO_BUILD_JOBS='2', CARGO_PROFILE_RELEASE_LTO='thin',
                      CARGO_PROFILE_RELEASE_CODEGEN_UNITS='8', CMAKE_BUILD_PARALLEL_LEVEL='2')
    patch = ROOT / 'patches/telemt-3.5.5-relay-backpressure.patch'
    # Historical patch context came from a Windows CRLF checkout; upstream Git
    # blobs use LF. Normalize only line terminators, preserving the stored patch.
    normalized_patch = output.parent / 'audit2-normalized.patch'
    normalized_patch.write_bytes(patch.read_bytes().replace(b'\r\n', b'\n'))
    run('git', 'apply', '--check', str(normalized_patch), cwd=source)
    run('git', 'apply', str(normalized_patch), cwd=source)
    helper = source / 'src/proxy/relay/adaptive_copy/flushed_copy.rs'
    assert digest(helper) == digest(ROOT / 'patches/flushed_copy.rs')
    manifest = {'classification': 'CI_EXPERIMENTAL_CORE_PAIR', 'source_commit': SOURCE_COMMIT,
                'project_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                'rust': rust, 'jobs': 2, 'lto': 'thin', 'codegen_units': 8,
                'base_patch_sha256': digest(patch),
                'candidate_kind': args.candidate,
                'candidate_helper_sha256': digest(ROOT / {'fair':'patches/fair_copy.rs','queue':'patches/unsent_queue.rs','balanced':'patches/low_rtt_receive.rs'}[args.candidate]),
                'builder_sha256': digest(Path(__file__)),
                'normalized_patch_sha256': digest(normalized_patch),
                'default_installer_changed': False, 'binaries': {}}
    candidate_version = {'fair':'3.5.5+tdh.fair1','queue':'3.5.5+tdh.queue1','balanced':'3.5.5+tdh.balanced1'}[args.candidate]
    variants = [('control', '3.5.5+tdh.audit2'), (args.candidate, candidate_version)]
    if args.candidate == 'queue':
        variants.insert(0, ('fixed', '3.5.5+tdh.audit1'))
    if args.reference_archive:
        reference = Path(args.reference_archive).resolve()
        assert digest(reference) == 'cc1881e3c5af3eef808650cce754f9bdcea6e2f6ffdb27a1e860aa5224be8b90'
        with tarfile.open(reference) as archive:
            old = json.load(archive.extractfile('manifest.json'))
            assert old['project_commit'] == '38da0fbf5b8c24913be7bfb61922a2e80fc957e4'
            assert old['rust'] == rust and old['lto'] == 'thin' and old['codegen_units'] == 8
            for name in ('fixed','control','queue'):
                target = output/('telemt-'+name)
                with target.open('xb') as stream:
                    shutil.copyfileobj(archive.extractfile('telemt-'+name), stream)
                target.chmod(0o755)
                assert digest(target) == old['binaries'][name]['sha256']
                manifest['binaries'][name] = old['binaries'][name]
        manifest['reference_archive_sha256'] = digest(reference)
        manifest['reference_build_commit'] = old['project_commit']
        variants = [(args.candidate, candidate_version)]
    socket_source = source / 'src/transport/socket.rs'
    automatic_socket_source = socket_source.read_text()
    previous_version = '3.5.5+tdh.audit2'
    for name, version in variants:
        if name == 'fixed':
            marker = '    // Preserve bounded kernel TCP autotuning on high-RTT relay paths.\n'
            assert automatic_socket_source.count(marker) == 2
            socket_source.write_text(automatic_socket_source.replace(marker,
                '    socket.set_recv_buffer_size(DEFAULT_SOCKET_BUFFER_BYTES)?;\n'
                '    socket.set_send_buffer_size(DEFAULT_SOCKET_BUFFER_BYTES)?;\n'))
        elif name == 'control':
            socket_source.write_text(automatic_socket_source)
        else:
            if name == 'fair':
                helper.write_bytes((ROOT / 'patches/fair_copy.rs').read_bytes())
            else:
                target = source / 'src/transport/socket/unsent_queue.rs'
                target.write_bytes((ROOT / 'patches/unsent_queue.rs').read_bytes())
                socket_source = source / 'src/transport/socket.rs'
                text = socket_source.read_text()
                prefix, body = text.split('pub fn create_outgoing_socket_bound(', 1)
                marker = '    socket.set_tcp_nodelay(true)?;\n'
                assert marker in body
                body = body.replace(marker, marker + '    #[cfg(target_os = "linux")]\n'
                    '    {\n        use std::os::fd::AsRawFd;\n'
                    '        unsent_queue::configure_unsent_queue(socket.as_raw_fd())?;\n    }\n', 1)
                combined = prefix + 'pub fn create_outgoing_socket_bound(' + body
                first_line, rest = combined.split('\n', 1)
                assert first_line.startswith('//!')
                socket_source.write_text(first_line + '\n#[cfg(target_os = "linux")]\nmod unsent_queue;\n' + rest)
                if name == 'balanced':
                    (source/'src/transport/socket/low_rtt_receive.rs').write_bytes((ROOT/'patches/low_rtt_receive.rs').read_bytes())
                    text = socket_source.read_text()
                    prefix, body = text.split('pub fn configure_client_socket(', 1)
                    marker = '    // Preserve bounded kernel TCP autotuning on high-RTT relay paths.\n'
                    assert marker in body
                    body = body.replace(marker, marker + '    #[cfg(target_os = "linux")]\n'
                        '    {\n        use std::os::fd::AsRawFd;\n'
                        '        low_rtt_receive::configure_low_rtt_receive(socket.as_raw_fd())?;\n    }\n', 1)
                    combined = prefix + 'pub fn configure_client_socket(' + body
                    first, rest = combined.split('\n', 1)
                    socket_source.write_text(first + '\n#[cfg(target_os = "linux")]\nmod low_rtt_receive;\n' + rest)
        for filename in ('Cargo.toml', 'Cargo.lock'):
            path = source / filename
            text = path.read_text()
            assert text.count(f'version = "{previous_version}"') == 1
            path.write_text(text.replace(f'version = "{previous_version}"', f'version = "{version}"'))
        previous_version = version
        run('cargo', 'build', '--release', '--locked', '--bin', 'telemt', cwd=source, timeout=1200)
        binary = source / 'target/release/telemt'
        actual = subprocess.check_output([str(binary), '--version'], text=True, timeout=5).strip()
        assert actual == 'telemt ' + version
        target = output / ('telemt-' + name)
        shutil.copyfile(binary, target)
        target.chmod(0o755)
        manifest['binaries'][name] = {'sha256': digest(target), 'version': actual,
                                      'bytes': target.stat().st_size}
    for filename in ('LICENSE',):
        shutil.copyfile(source / filename, output / filename)
    (output / 'NOTICE.txt').write_text('Unofficial experimental builds of Telemt 3.5.5. Original license retained.\n'
        'Control applies audit2. Candidate changes only the declared relay or outgoing unsent-queue policy and version marking.\n'
        'No endorsement by Telemt maintainers. Not a production release.\n')
    shutil.copyfile(patch, output / 'base.patch')
    shutil.copyfile(ROOT / 'patches/fair_copy.rs', output / 'fair_copy.rs')
    shutil.copyfile(ROOT / 'patches/unsent_queue.rs', output / 'unsent_queue.rs')
    shutil.copyfile(ROOT / 'patches/low_rtt_receive.rs', output / 'low_rtt_receive.rs')
    shutil.copyfile(Path(__file__), output / 'build_relay_candidate.py')
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    archive = output.with_suffix('.tar.gz')
    with tarfile.open(archive, 'x:gz') as stream:
        for path in sorted(output.iterdir()):
            stream.add(path, arcname=path.name, recursive=False)
    archive.with_suffix('.gz.sha256').write_text(digest(archive) + '  ' + archive.name + '\n')


if __name__ == '__main__':
    main()
