"""One CI-only fairness candidate from the already reviewed balanced1 source."""
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from stable_core_bundle import bundle, UPSTREAM, PINS
from e2e_lab import FullLab

ROOT=Path(__file__).resolve().parents[1]
COPY_SHA='45f4bab471a2b98405a809cc1be7740b635c74149e6687854f5a89b2df9140cd'
VERSION='telemt 3.5.17+tdh.fair1'
PROOF_SHA='e89fcde95fb09556883fc7ef7105b9ab4cc38ce688fa31205a034b4050d4f31f'


def run(argv,source,timeout=1200):subprocess.run(argv,cwd=source,check=True,timeout=timeout)
def sha(data):return hashlib.sha256(data).hexdigest()


def main(args):
    source=Path(args.source).resolve();output=Path(args.output).resolve()
    if VERSION not in FullLab.EXPERIMENTAL_VERSIONS:raise RuntimeError('candidate version missing from protocol harness')
    if os.environ.get('GITHUB_ACTIONS')!='true' or os.name!='posix':raise RuntimeError('disposable Linux CI only')
    if subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True).strip()!=UPSTREAM:
        raise RuntimeError('upstream identity drift')
    if subprocess.check_output(['git','status','--porcelain'],cwd=source):raise RuntimeError('dirty source refused')
    rust=subprocess.check_output(['rustc','-V'],text=True).strip()
    if rust.split()[1]!='1.99.1':raise RuntimeError('toolchain drift')
    output.mkdir(mode=0o700)
    archive=output/'reference.tar.gz';bundle(args.reference,archive)
    with tarfile.open(archive,'r:gz') as packed:
        patch=packed.extractfile('source.patch').read()
    if sha(patch)!='87a734aa1cdc409c902544f73403c252beaa6c2f46adcaca7614c485fb00a8d8':raise RuntimeError('reference source patch drift')
    subprocess.run(['git','apply','-'],cwd=source,input=patch,check=True,timeout=10)
    target=source/'src/proxy/relay/adaptive_copy/copy.rs'
    original=target.read_bytes()
    if sha(original)!=COPY_SHA:raise RuntimeError('upstream copy source drift')
    before={name:(source/name).read_bytes() for name in ('Cargo.toml','Cargo.lock','src/proxy/relay/adaptive_copy/copy.rs')}
    target.write_bytes((ROOT/'patches/stable_fair_copy.rs').read_bytes())
    for filename in ('Cargo.toml','Cargo.lock'):
        path=source/filename;text=path.read_text()
        old='version = "3.5.17+tdh.balanced1"'
        if text.count(old)!=1:raise RuntimeError('package identity drift')
        path.write_text(text.replace(old,'version = "3.5.17+tdh.fair1"'))
    reused=False
    if args.verified_manifest:
        proof=Path(args.verified_manifest).read_bytes()
        if sha(proof)!=PROOF_SHA:raise RuntimeError('unverified prior test record')
        prior=json.loads(proof)
        expected={'upstream_commit':UPSTREAM,'rust':rust,'reference_binary_sha256':PINS['telemt-balanced'],
            'version':VERSION,'copy_before_sha256':COPY_SHA,'copy_after_sha256':sha(target.read_bytes()),
            'quantum_bytes':65536,'lto':'thin','codegen_units':8}
        if any(prior.get(key)!=value for key,value in expected.items()):raise RuntimeError('tested Rust source/toolchain changed')
        reused=True
    os.environ.update(CARGO_BUILD_JOBS='3',CARGO_PROFILE_RELEASE_LTO='thin',CARGO_PROFILE_RELEASE_CODEGEN_UNITS='8',CMAKE_BUILD_PARALLEL_LEVEL='3')
    run(['cargo','build','--locked','--release','--bin','telemt'],source)
    if not reused:
        run(['cargo','test','--locked','--release','--bin','telemt','proxy::relay::adaptive_copy::tests'],source)
    else:
        print('Upstream 9 tests: NOT RUN here; reused exact Rust source/toolchain from verified run42574699893',flush=True)
    binary=source/'target/release/telemt'
    actual=subprocess.check_output([str(binary),'--version'],text=True,timeout=5).strip()
    if actual!=VERSION:raise RuntimeError('built version mismatch')
    shutil.copyfile(binary,output/'telemt-fair');(output/'telemt-fair').chmod(0o755)
    changes=''.join(''.join(difflib.unified_diff(data.decode().splitlines(keepends=True),
        (source/name).read_text().splitlines(keepends=True),fromfile='a/'+name,tofile='b/'+name)) for name,data in before.items())
    # Apply the retained full reference.patch first, then this incremental patch.
    (output/'candidate.patch').write_text(changes)
    (output/'reference.patch').write_bytes(patch)
    manifest={'upstream_commit':UPSTREAM,'manager_commit':os.environ['GITHUB_SHA'],'rust':rust,
        'reference_binary_sha256':PINS['telemt-balanced'],'version':actual,
        'binary_sha256':sha(binary.read_bytes()),'copy_before_sha256':sha(original),
        'copy_after_sha256':sha(target.read_bytes()),'quantum_bytes':65536,'production_enabled':False,
        'lto':'thin','codegen_units':8,'upstream_copy_tests':'same-source proof42574699893' if reused else '9-test subset executed',
        'test_proof_sha256':PROOF_SHA if reused else None}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    archive.unlink()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True);parser.add_argument('--reference',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--verified-manifest')
    main(parser.parse_args())
