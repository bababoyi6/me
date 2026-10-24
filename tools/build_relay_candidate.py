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
    run('git', 'apply', '--check', str(patch), cwd=source)
    run('git', 'apply', str(patch), cwd=source)
    helper = source / 'src/proxy/relay/adaptive_copy/flushed_copy.rs'
    assert digest(helper) == digest(ROOT / 'patches/flushed_copy.rs')
    manifest = {'classification': 'CI_EXPERIMENTAL_CORE_PAIR', 'source_commit': SOURCE_COMMIT,
                'project_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                'rust': rust, 'jobs': 2, 'lto': 'thin', 'codegen_units': 8,
                'base_patch_sha256': digest(patch), 'candidate_helper_sha256': digest(ROOT / 'patches/fair_copy.rs'),
                'default_installer_changed': False, 'binaries': {}}
    for name, version in (('control', '3.5.5+tdh.audit2'), ('fair', '3.5.5+tdh.fair1')):
        if name == 'fair':
            helper.write_bytes((ROOT / 'patches/fair_copy.rs').read_bytes())
            for filename in ('Cargo.toml', 'Cargo.lock'):
                path = source / filename
                text = path.read_text()
                assert text.count('version = "3.5.5+tdh.audit2"') == 1
                path.write_text(text.replace('version = "3.5.5+tdh.audit2"', f'version = "{version}"'))
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
        'Control applies the repository audit2 patch. fair adds the tracked fair_copy.rs helper and version marking.\n'
        'No endorsement by Telemt maintainers. Not a production release.\n')
    shutil.copyfile(patch, output / 'base.patch')
    shutil.copyfile(ROOT / 'patches/fair_copy.rs', output / 'fair_copy.rs')
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    archive = output.with_suffix('.tar.gz')
    with tarfile.open(archive, 'x:gz') as stream:
        for path in sorted(output.iterdir()):
            stream.add(path, arcname=path.name, recursive=False)
    archive.with_suffix('.gz.sha256').write_text(digest(archive) + '  ' + archive.name + '\n')


if __name__ == '__main__':
    main()
