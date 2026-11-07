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
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from core_install import archive_payload, durable_file, fresh_install
from core_transaction import digest
from core_upgrade import BackendService, upgrade, rollback
from e2e_lab import FullLab, MockDc
from lab_config import generate_telemt
from stable_acceptance import PINS
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
    parser.add_argument('--stable-archive', required=True)
    parser.add_argument('--balanced2-archive', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError('root required')
    unit = 'tdh-upgrade-test-'+uuid.uuid4().hex+'.service'
    unit_path = Path('/run/systemd/system')/unit
    report = {'scope': 'WSL isolated namespace; real systemd, real release binaries; synthetic identities/DC only',
              'accepted': False}
    agent = None
    dc = MockDc(socket_timeout=25)
    with tempfile.TemporaryDirectory(prefix='tdh-core-system-') as temp:
        root = Path(temp)
        root.chmod(0o755)
        try:
            binary = root/'telemt'
            content, _ = archive_payload(args.balanced2_archive, PINS['balanced2'][0],
                                          PINS['balanced2'][2].removeprefix('telemt '), PINS['balanced2'][1], True)
            durable_file(binary, content, 0o755)
            store = root/'transaction'
            store.mkdir(mode=0o700)
            fresh = root/'fresh'
            fresh.mkdir(mode=0o755)
            # Map the installer identity lookup to an existing unprivileged UID;
            # do not create a host account just for a test. All other install
            # operations, archive validation and --version execution are real.
            with patch('core_install.pwd.getpwnam', return_value=SimpleNamespace(pw_uid=65534, pw_gid=65534)):
                report['fresh_official_install'] = fresh_install(args.stable_archive, fresh/'telemt',
                    PINS['stable'][0], '3.5.16', PINS['stable'][1], False)
            report['fresh_official_binary_sha256_match'] = digest(fresh/'telemt') == PINS['stable'][1]
            listen, api, health_port = port(), port(), port()
            config = generate_telemt(root/'config', root/'work', listen, api)
            text = config.read_text().replace('tls_emulation = true', 'tls_emulation = false')
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
            service.start()
            if not service.ready():
                raise RuntimeError('baseline service not ready')
            protected = [root/'config', unit_path]
            old_config = digest(config)
            report['upgrade'] = upgrade(binary, store, args.stable_archive, PINS['stable'][0],
                                         PINS['stable'][1], '3.5.16', protected, service)
            report['repeat_upgrade'] = upgrade(binary, store, args.stable_archive, PINS['stable'][0],
                                                PINS['stable'][1], '3.5.16', protected, service)
            report['unauthenticated_api_status'] = request(api, 'GET', '/v1/users', authenticated=False)[0]
            report['read_only_api_write_status'] = request(api, 'POST', '/v1/users')[0]
            # Reuse the actual protocol transfer method against the unit's listener.
            endpoint = SimpleNamespace(real_dc=False, listen=listen, front=listen)
            report['transfers'] = [FullLab.transfer(endpoint, domain, direction, 4096, 1000000,
                                                    direct=True, persistent=True)
                                   for domain in ('apple.com', 'gs.apple.com') for direction in ('U', 'D')]
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
            agent = subprocess.Popen(['python3', str(Path(__file__).resolve().parents[1]/'tools/health_agent.py')],
                                     env=agent_env, user=65534, group=65534, extra_groups=[],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            report['production_agent_cold_ready_seconds'] = wait_for(lambda: agent_state(health_port, b'up'), 35)
            service.stop()
            report['production_agent_core_down_seconds'] = wait_for(lambda: agent_state(health_port, b'down'), 10)
            service.start()
            if not service.ready():
                raise RuntimeError('manually restarted core not ready')
            report['production_agent_recovery_seconds'] = wait_for(lambda: agent_state(health_port, b'up'), 25)
            report['rollback'] = rollback(binary, store, protected, service)
            report['old_binary_restored'] = digest(binary) == PINS['balanced2'][1]
            report['config_and_token_unchanged'] = digest(config) == old_config and token.read_text() == TOKEN+'\n'
            report['accepted'] = (report['old_binary_restored'] and report['config_and_token_unchanged']
                                  and report['unauthenticated_api_status'] in (401, 403)
                                  and report['read_only_api_write_status'] == 403)
        except Exception as error:
            report['error_type'] = type(error).__name__
            report['error_stage'] = ('after_'+next(reversed(report)) if report else 'setup')
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
