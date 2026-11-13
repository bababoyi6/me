"""CI-only native socket candidates from fixed upstream Telemt commits."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
COMMIT='66708d2866f3f3a4541c4fa67984d408e71a8af4'
SECURITY_COMMIT='b594ee55270798fe21f23b721b50eab46c2e0bec'


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def run(argv,source,timeout=1200):
    subprocess.run(argv,cwd=source,check=True,timeout=timeout)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--security-main', action='store_true',
                        help='Build only the socket candidate on the pinned post-3.5.17 upstream commit')
    parser.add_argument('--build-only', action='store_true',
                        help='Build the same fixed candidate for a follow-up performance run')
    parser.add_argument('--client-unsent-limit', action='store_true',
                        help='Build a separate candidate that also bounds unsent bytes toward the entry')
    args=parser.parse_args();source=Path(args.source).resolve();output=Path(args.output).resolve()
    if args.build_only and not args.security_main:
        parser.error('--build-only requires --security-main')
    if args.client_unsent_limit and not args.security_main:
        parser.error('--client-unsent-limit requires --security-main')
    if sys.platform!='linux' or os.environ.get('GITHUB_ACTIONS')!='true':raise RuntimeError('disposable CI only')
    upstream = SECURITY_COMMIT if args.security_main else COMMIT
    if subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True).strip()!=upstream:
        raise RuntimeError('upstream commit mismatch')
    if subprocess.check_output(['git','status','--porcelain'],cwd=source):raise RuntimeError('dirty upstream refused')
    rust=subprocess.check_output(['rustc','-V'],text=True).strip()
    if rust.split()[1]!='1.99.1':raise RuntimeError('pinned Rust required')
    output.mkdir(mode=0o700)
    os.environ.update(CARGO_BUILD_JOBS='3',CARGO_PROFILE_RELEASE_LTO='thin',CARGO_PROFILE_RELEASE_CODEGEN_UNITS='8',CMAKE_BUILD_PARALLEL_LEVEL='3')
    manifest={'upstream_commit':upstream,'manager_commit':os.environ['GITHUB_SHA'],'rust':rust,
        'lto':'thin','codegen_units':8,'production_default_changed':False,'binaries':{},
        'builder_sha256':sha(Path(__file__)),'helpers':{},'scope':'unofficial synthetic lab candidates only; no publication or deployment'}
    previous='3.5.17'
    socket=source/'src/transport/socket.rs'
    original=socket.read_text()
    variants = [('control','3.5.17+tdh.control1'),('balanced','3.5.17+tdh.balanced1')]
    if args.security_main:
        variants = [('balanced', '3.5.17+tdh.main.b594ee5.balanced1')]
        manifest['cargo'] = subprocess.check_output(['cargo', '--version'], text=True).strip()
        manifest['architecture'] = os.uname().machine
    if args.client_unsent_limit:
        variants = [('balanced', '3.5.17+tdh.main.b594ee5.clientlowat1')]
        manifest['client_tcp_notsent_lowat_bytes'] = 256 * 1024
    for name,version in variants:
        if name=='balanced':
            text=original
            for helper in ('low_rtt_receive','unsent_queue_bulk'):
                target=source/'src/transport/socket'/(helper+'.rs')
                target.write_bytes((ROOT/'patches'/(helper+'.rs')).read_bytes())
                manifest['helpers'][helper]=sha(target)
            text=text.replace('mod fragmented_send;','mod fragmented_send;\n#[cfg(target_os = "linux")]\nmod low_rtt_receive;\n#[cfg(target_os = "linux")]\nmod unsent_queue_bulk;',1)
            prefix,body=text.split('pub fn configure_client_socket(',1)
            fixed='    socket.set_recv_buffer_size(DEFAULT_SOCKET_BUFFER_BYTES)?;\n    socket.set_send_buffer_size(DEFAULT_SOCKET_BUFFER_BYTES)?;'
            client,outgoing=body.split('pub fn create_outgoing_socket_bound(',1)
            if client.count(fixed)!=1 or outgoing.count(fixed)!=1:raise RuntimeError('socket context drift')
            fallback='    #[cfg(not(target_os = "linux"))]\n    {\n'+fixed+'\n    }'
            client_policy = (
                '    // Linux: reuse balanced2 low-RTT receive guard; retain automatic send sizing.\n'
                '    #[cfg(target_os = "linux")]\n    {\n        use std::os::fd::AsRawFd;\n'
                '        low_rtt_receive::configure_low_rtt_receive(socket.as_raw_fd())?;\n'
            )
            if args.client_unsent_limit:
                client_policy += '        unsent_queue_bulk::configure_unsent_queue(socket.as_raw_fd())?;\n'
            client=client.replace(fixed,client_policy+'    }\n'+fallback,1)
            outgoing=outgoing.replace(fixed,'    // Linux: preserve autotuning and bound unsent data, not in-flight bytes.\n'
                '    #[cfg(target_os = "linux")]\n    {\n        use std::os::fd::AsRawFd;\n'
                '        unsent_queue_bulk::configure_unsent_queue(socket.as_raw_fd())?;\n    }\n'+fallback,1)
            socket.write_text(prefix+'pub fn configure_client_socket('+client+'pub fn create_outgoing_socket_bound('+outgoing)
            tests=source/'src/transport/socket/tests.rs'
            tests.write_text(tests.read_text()+'\n'+(ROOT/'patches/stable_socket_tests.rs').read_text())
        for filename in ('Cargo.toml','Cargo.lock'):
            path=source/filename;text=path.read_text();old=f'version = "{previous}"'
            if text.count(old)!=1:raise RuntimeError('package identity drift')
            path.write_text(text.replace(old,f'version = "{version}"'))
        previous=version
        if args.security_main:
            manifest['cargo_lock_sha256'] = sha(source / 'Cargo.lock')
            if not args.build_only:
                run(['cargo','test','--locked','--release','--bin','telemt','inner_content_type_position_is_not_constant'],source)
                run(['cargo','test','--locked','--release','--bin','telemt','maestro::tls_bootstrap::tests::'],source)
        run(['cargo','build','--locked','--release','--bin','telemt'],source)
        if not args.build_only:
            run(['cargo','test','--locked','--release','--bin','telemt','transport::socket::tests::'],source)
        binary=source/'target/release/telemt'
        actual=subprocess.check_output([str(binary),'--version'],text=True,timeout=5).strip()
        if actual!='telemt '+version:raise RuntimeError('built identity mismatch')
        target=output/('telemt-'+name);shutil.copyfile(binary,target);target.chmod(0o755)
        manifest['binaries'][name]={'sha256':sha(target),'version':actual,'bytes':target.stat().st_size}
        if name=='balanced':
            (output/'candidate.patch').write_bytes(subprocess.check_output(['git','diff','--','Cargo.toml','Cargo.lock','src/transport/socket.rs','src/transport/socket/tests.rs'],cwd=source))
    for name in ('LICENSE',):shutil.copyfile(source/name,output/name)
    for name in ('low_rtt_receive','unsent_queue_bulk'):shutil.copyfile(ROOT/'patches'/(name+'.rs'),output/(name+'.rs'))
    (output/'NOTICE.txt').write_text(
        f'Unofficial Telemt lab build from {upstream}. Original license and copyright retained.\n'
        'Includes local Linux socket buffering changes. Experimental build; no upstream endorsement.\n'
    )
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')


if __name__=='__main__':main()
