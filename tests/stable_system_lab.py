#!/usr/bin/env python3
"""Real systemd/core upgrade and production-health-agent experiment, loopback only.

Uses a fresh, owned transient unit under /run; never touches managed/production
unit names. Invoke as root in a disposable network namespace with loopback up.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from core_install import archive_payload, durable_file, fresh_install
from core_transaction import digest
from core_upgrade import BackendService, upgrade, rollback, refresh_connect_policy
from e2e_lab import FullLab, MockDc
from lab_config import generate_telemt
from stable_acceptance import PINS
from release_pins import RELEASES, NATIVE_BASES
from telemt_probe import port, TOKEN, request
from types import SimpleNamespace
from unittest.mock import patch


def systemctl(*args):
    return subprocess.run(['systemctl', *args], check=True, capture_output=True, text=True, timeout=30).stdout.strip()


def wait_for(predicate, seconds):
    started = time.monotonic()
    while time.monotonic()-started < seconds:
        if predicate():
            return time.monotonic()-started
        time.sleep(.2)
    raise RuntimeError('bounded readiness deadline')


def agent_state(port_number, expected):
    try:
        with socket.create_connection(('127.0.0.1', port_number), timeout=1) as connection:
            connection.settimeout(1)
            return connection.recv(16).strip() == expected
    except OSError:
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    candidate=parser.add_mutually_exclusive_group(required=True)
    candidate.add_argument('--stable-archive')
    candidate.add_argument('--candidate-directory')
    parser.add_argument('--candidate-package')
    baseline=parser.add_mutually_exclusive_group(required=True)
    baseline.add_argument('--balanced2-archive')
    baseline.add_argument('--previous-stable-archive')
    parser.add_argument('--output', required=True)
    parser.add_argument('--stable-release',choices=tuple(RELEASES),default='3.5.16')
    args = parser.parse_args()
    if args.candidate_package and not args.candidate_directory:
        parser.error('candidate package requires the pinned native directory')
    baseline_pins=RELEASES['3.5.16'] if args.previous_stable_archive else PINS['balanced2']
    baseline_archive=args.previous_stable_archive or args.balanced2_archive
    if os.geteuid() != 0:
        raise RuntimeError('root required')
    unit = 'tdh-upgrade-test-'+uuid.uuid4().hex+'.service'
    unit_path = Path('/run/systemd/system')/unit
    report = {'scope': 'isolated Linux namespace; real systemd and release binaries; synthetic identities/DC only',
              'baseline_core_version':baseline_pins[2],
              'os': platform.freedesktop_os_release().get('PRETTY_NAME'),
              'kernel': os.uname().release, 'accepted': False}
    stage = 'setup'
    agent = None
    dc = MockDc(socket_timeout=25)
    with tempfile.TemporaryDirectory(prefix='tdh-core-system-') as temp:
        root = Path(temp)
        root.chmod(0o755)
        try:
            candidate_archive=args.stable_archive
            candidate_pins=RELEASES[args.stable_release]
            candidate_version=args.stable_release
            if args.candidate_directory:
                if os.environ.get('GITHUB_ACTIONS')!='true' or os.stat('/proc/self/ns/net').st_ino==os.stat('/proc/1/ns/net').st_ino:
                    raise RuntimeError('native candidate requires owned CI namespace')
                bundle=Path(args.candidate_directory)
                manifest=json.loads((bundle/'manifest.json').read_text())
                pin=manifest['binaries']['balanced']
                base=NATIVE_BASES.get(manifest['upstream_commit'])
                if (base is None or
                        pin['version']!='telemt '+base+'+tdh.balanced1' or
                        digest(bundle/'telemt-balanced')!=pin['sha256']):
                    raise RuntimeError('native candidate identity mismatch')
                candidate_archive=Path(args.candidate_package) if args.candidate_package else root/'native.tar.gz'
                if not args.candidate_package:
                    with tarfile.open(candidate_archive,'x:gz') as archive:
                        archive.add(bundle/'telemt-balanced',arcname='telemt',recursive=False)
                candidate_version=pin['version'].removeprefix('telemt ')
                candidate_pins=(digest(candidate_archive),pin['sha256'],pin['version'])
                report['native_manifest']=manifest
            binary = root/'telemt'
            content, _ = archive_payload(baseline_archive, baseline_pins[0],
                                          baseline_pins[2].removeprefix('telemt '), baseline_pins[1], not bool(args.previous_stable_archive))
            durable_file(binary, content, 0o755)
            store = root/'transaction'
            store.mkdir(mode=0o700)
            fresh = root/'fresh'
            fresh.mkdir(mode=0o755)
            # Map the installer identity lookup to an existing unprivileged UID;
            # do not create a host account just for a test. All other install
            # operations, archive validation and --version execution are real.
            with patch('core_install.pwd.getpwnam', return_value=SimpleNamespace(pw_uid=65534, pw_gid=65534)):
                report['fresh_candidate_install' if args.candidate_directory else 'fresh_official_install'] = fresh_install(candidate_archive, fresh/'telemt',
                    candidate_pins[0], candidate_version, candidate_pins[1], bool(args.candidate_package))
            report['fresh_candidate_binary_sha256_match' if args.candidate_directory else 'fresh_official_binary_sha256_match'] = digest(fresh/'telemt') == candidate_pins[1]
            listen, api, health_port = port(), port(), port()
            config = generate_telemt(root/'config', root/'work', listen, api)
            text = config.read_text().replace('tls_emulation = true', 'tls_emulation = false')
            # Legacy configs have no explicit connection defaults.
            for line in ('tg_connect = 4\n','upstream_connect_retry_attempts = 2\n','upstream_connect_budget_ms = 9000\n'):
                text = text.replace(line,'')
            text = text.replace('[network]\n', '[network]\nstun_use = false\nhttp_ip_detect_urls = []\n')
            text += '\n[dc_overrides]\n'+''.join(f'"{dcid}" = ["127.0.0.1:{dc.server_address[1]}"]\n' for dcid in range(1, 6))
            config.write_text(text)
            token = root/'config/api.token'
            token.write_text(TOKEN+'\n')
            for folder in (root/'config', root/'work'):
                folder.chmod(0o750)
                os.chown(folder, 0, 65534)
            for path in (config, token):
                path.chmod(0o640)
                os.chown(path, 0, 65534)
            for path in (root/'work/telemt', root/'work/telemt/tlsfront'):
                os.chown(path, 65534, 65534)
            unit_path.write_text(f'''[Unit]
Description=Owned bounded Telemt upgrade acceptance
[Service]
Type=exec
User=nobody
Group=nogroup
WorkingDirectory={root}/work/telemt
ExecStart={binary} {config}
NetworkNamespacePath=/proc/{os.getpid()}/ns/net
Restart=always
RestartSec=1
CPUQuota=100%
MemoryMax=512M
MemorySwapMax=0
RuntimeMaxSec=240
NoNewPrivileges=true
StandardOutput=null
StandardError=null
''')
            unit_path.chmod(0o644)
            systemctl('daemon-reload')
            service = BackendService(binary, config, root/'work/telemt', token, api, unit=unit, user='nobody')
            stage = 'baseline_start'
            service.start()
            if not service.ready():
                raise RuntimeError('baseline service not ready')
            protected = [root/'config', unit_path]
            old_config = digest(config)
            stage = 'core_upgrade'
            report['upgrade'] = upgrade(binary, store, candidate_archive, candidate_pins[0],
                                         candidate_pins[1], candidate_version, protected, service, audited=bool(args.candidate_package))
            report['repeat_upgrade'] = upgrade(binary, store, candidate_archive, candidate_pins[0],
                                                candidate_pins[1], candidate_version, protected, service, audited=bool(args.candidate_package))
            stage = 'connect_policy'
            report['connect_refresh'] = refresh_connect_policy(root,service)
            report['repeat_connect_refresh'] = refresh_connect_policy(root,service)
            report['connect_config_private'] = (config.stat().st_mode & 0o777)==0o640 and config.stat().st_gid==65534
            report['unauthenticated_api_status'] = request(api, 'GET', '/v1/users', authenticated=False)[0]
            report['read_only_api_write_status'] = request(api, 'POST', '/v1/users')[0]
            # Reuse the actual protocol transfer method against the unit's listener.
            stage = 'native_protocol'
            endpoint = SimpleNamespace(real_dc=False, listen=listen, front=listen)
            report['transfers'] = [FullLab.transfer(endpoint, domain, direction, 4096, 1000000,
                                                    direct=True, persistent=True)
                                   for domain in ('apple.com', 'gs.apple.com') for direction in ('U', 'D')]
            stage = 'systemd_crash_recovery'
            pid = int(systemctl('show', unit, '--property=MainPID', '--value'))
            os.kill(pid, signal.SIGKILL)
            report['systemd_crash_recovery_seconds'] = wait_for(
                lambda: int(systemctl('show', unit, '--property=MainPID', '--value')) not in (0, pid)
                        and service.running_binary_matches(), 15)
            if not service.ready():
                raise RuntimeError('restarted core not ready')
            report['systemd_restart_count'] = int(systemctl('show', unit, '--property=NRestarts', '--value'))
            agent_env = dict(os.environ, TDH_BIND_IP='127.0.0.1', TDH_AGENT_PORT=str(health_port),
                             TDH_API_PORT=str(api), TDH_TELEMT_PORT=str(listen), TDH_DC_IPV4='127.0.0.1',
                             TDH_DC_PORT=str(dc.server_address[1]), TDH_API_TOKEN_FILE=str(token))
            stage = 'health_agent_cold_start'
            source = Path(__file__).resolve().parents[1]/'tools/health_agent.py'
            report['repository_agent_readable_by_service_uid'] = subprocess.run(
                ['/usr/bin/test', '-r', str(source)], user=65534, group=65534, extra_groups=[],
                capture_output=True, timeout=3).returncode == 0
            # GitHub's runner home need not be traversable by nobody. Stage the
            # exact public source beside the other owned test files, rather than
            # relaxing runner directory permissions or running the agent as root.
            staged_agent = root/'health_agent.py'
            durable_file(staged_agent, source.read_bytes(), 0o644)
            report['health_agent_source_sha256'] = digest(staged_agent)
            agent = subprocess.Popen(['python3', str(staged_agent)],
                                     env=agent_env, user=65534, group=65534, extra_groups=[],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            report['production_agent_cold_ready_seconds'] = wait_for(lambda: agent_state(health_port, b'up'), 35)
            stage = 'health_agent_core_down'
            service.stop()
            report['production_agent_core_down_seconds'] = wait_for(lambda: agent_state(health_port, b'down'), 10)
            stage = 'health_agent_recovery'
            service.start()
            if not service.ready():
                raise RuntimeError('manually restarted core not ready')
            report['production_agent_recovery_seconds'] = wait_for(lambda: agent_state(health_port, b'up'), 25)
            stage = 'core_rollback'
            report['connect_rollback'] = refresh_connect_policy(root,service,True)
            report['connect_original_bytes_restored'] = digest(config)==old_config
            report['rollback'] = rollback(binary, store, protected, service)
            if args.candidate_package:
                cached=store/('audited-core-'+candidate_pins[1]+'.tar.gz')
                report['audited_archive_private_and_preserved_after_rollback']=(cached.is_file() and
                    (cached.stat().st_mode & 0o777)==0o600 and digest(cached)==candidate_pins[0])
                if not report['audited_archive_private_and_preserved_after_rollback']:
                    raise RuntimeError('audited source/license cache failed rollback preservation')
            report['old_binary_restored'] = digest(binary) == baseline_pins[1]
            report['config_and_token_unchanged'] = digest(config) == old_config and token.read_text() == TOKEN+'\n'
            report['accepted'] = (report['old_binary_restored'] and report['config_and_token_unchanged']
                                  and report['connect_config_private'] and report['connect_original_bytes_restored']
                                  and report['unauthenticated_api_status'] in (401, 403)
                                  and report['read_only_api_write_status'] == 403)
        except Exception as error:
            report['error_type'] = type(error).__name__
            report['error_stage'] = stage
            report['health_agent_process_exit'] = agent.poll() if agent is not None else None
            raise
        finally:
            if agent is not None:
                agent.terminate()
                agent.wait(timeout=5)
            subprocess.run(['systemctl', 'stop', unit], capture_output=True, timeout=30)
            unit_path.unlink(missing_ok=True)
            systemctl('daemon-reload')
            subprocess.run(['systemctl', 'reset-failed', unit], capture_output=True, timeout=5)
            dc.shutdown()
            dc.server_close()
            report['temporary_unit_removed'] = not unit_path.exists()
            Path(args.output).write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({'accepted': report['accepted'], 'unit_removed': report['temporary_unit_removed']}))
    return 0 if report['accepted'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
