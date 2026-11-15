"""Real loopback TLS source outage/recovery for the fixed old cache candidate."""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import tempfile
import time

from e2e_lab import FullLab
from paired_performance import resources, write
from release_pins import OLD_TLS_CACHE_SHA256, TLS_CANDIDATES
from telemt_probe import SECRETS, port, request
from tls_candidate_lab import candidate_binary

DOMAINS = tuple(SECRETS)


def metrics(port_number):
    connection = http.client.HTTPConnection('127.0.0.1', port_number, timeout=2)
    try:
        connection.request('GET', '/metrics')
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError('local metrics endpoint rejected the request')
        body = response.read(512 * 1024).decode('utf-8')
    finally:
        connection.close()
    result = {}
    for domain in DOMAINS:
        pattern = (r'telemt_tls_front_profile_info\{domain="' + re.escape(domain)
                   + r'",source="([a-z_]+)",is_default="(true|false)"')
        match = re.search(pattern, body)
        if match is None:
            raise RuntimeError('missing local TLS profile metric')
        result[domain] = {'source': match[1], 'is_default': match[2] == 'true'}
    return result


def wait_profiles(port_number, expected_default, deadline_seconds):
    deadline = time.monotonic() + deadline_seconds
    latest = None
    while time.monotonic() < deadline:
        try:
            latest = metrics(port_number)
            if all(row['is_default'] is expected_default for row in latest.values()):
                return latest
        except (OSError, RuntimeError):
            pass
        time.sleep(.2)
    raise RuntimeError(f'TLS profile state did not become {expected_default}: {latest}')


def transfers(lab):
    rows = []
    for domain in DOMAINS:
        for direction in ('U', 'D'):
            result = lab.transfer(domain, direction, 4096, 2500000,
                                  fragment=True, persistent=True)
            if result.get('sha256_verified') is not True:
                raise RuntimeError('synthetic transfer checksum failed')
            rows.append({'domain': domain, 'direction': direction,
                         'sha256_verified': True})
    return rows


def start_origin(directory, port_number):
    cert = directory / 'cert.pem'
    key = directory / 'key.pem'
    subprocess.run([
        'openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
        '-keyout', str(key), '-out', str(cert), '-days', '1',
        '-subj', '/CN=apple.com',
        '-addext', 'subjectAltName=DNS:apple.com,DNS:gs.apple.com',
    ], check=True, timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    key.chmod(0o600)
    origin = subprocess.Popen([
        'openssl', 's_server', '-accept', f'127.0.0.1:{port_number}',
        '-cert', str(cert), '-key', str(key), '-quiet',
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(.2)
    if origin.poll() is not None:
        raise RuntimeError('private TLS source exited before recovery')
    return origin


def stop(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def cache_files(lab):
    root = lab.root / 'work' / 'telemt' / 'tlsfront'
    result = {}
    for domain in DOMAINS:
        path = root / f'{domain}.json'
        metadata = path.lstat()
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or not 0 < metadata.st_size <= 1024 * 1024):
            raise RuntimeError('unsafe persisted TLS profile metadata')
        data = path.read_bytes()
        decoded = json.loads(data)
        source = decoded['behavior_profile']['source']
        if decoded['domain'] != domain or source == 'default':
            raise RuntimeError('persisted TLS profile identity mismatch')
        result[domain] = {'sha256': hashlib.sha256(data).hexdigest(),
                          'bytes': len(data), 'source': source, 'mode': '0600'}
    if any(path.name.startswith('.telemt-cache-') for path in root.iterdir()):
        raise RuntimeError('TLS cache temporary file remained after recovery')
    return result


def restart_core(lab):
    old = lab.telemt
    old.terminate()
    old.wait(timeout=5)
    lab.processes.remove(old)
    new = subprocess.Popen([
        str(lab.root / 'telemt'), str(lab.telemt_config),
    ], cwd=lab.root / 'work' / 'telemt', stdout=subprocess.DEVNULL,
       stderr=subprocess.DEVNULL, **lab.identity())
    lab.telemt = new
    lab.processes.append(new)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and new.poll() is None:
        try:
            if request(lab.api, 'GET', '/v1/health')[0] == 200:
                return
        except (OSError, ValueError):
            pass
        time.sleep(.1)
    raise RuntimeError('candidate failed to restart with persisted TLS cache')


def main(args):
    before = resources()
    if (os.geteuid() != 0 or os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.readlink('/proc/self/ns/net') == os.readlink('/proc/1/ns/net')
            or before['cpu_max'] != '100000 100000'
            or before['memory_max'] != str(512 * 1024**2)
            or before['swap_max'] != '0'):
        raise RuntimeError('bounded owned CI network namespace required')
    links = json.loads(subprocess.check_output(['ip', '-j', 'link', 'show'], text=True, timeout=5))
    if [entry['ifname'] for entry in links] != ['lo']:
        raise RuntimeError('private loopback-only namespace required')
    binary, pin = candidate_binary(args.candidate, 'oldcache', os.environ['TDH_SOURCE_COMMIT'])
    if pin['sha256'] != OLD_TLS_CACHE_SHA256 or pin['version'] != TLS_CANDIDATES['oldcache'][3]:
        raise RuntimeError('fixed TLS cache candidate changed')
    report = {'source_commit': os.environ['TDH_SOURCE_COMMIT'],
              'binary_sha256': pin['sha256'],
              'scope': 'isolated loopback real TLS source; synthetic DC and users only',
              'resources_before': before, 'accepted': False}
    origin = None
    lab = None
    try:
        with tempfile.TemporaryDirectory(prefix='tdh-tls-origin-', dir='/tmp') as name:
            origin_port = port()
            metrics_port = port()
            while origin_port == metrics_port:
                metrics_port = port()
            lab = FullLab(binary, offline_lab=True, synthetic_tls_front=True,
                          tls_origin_port=origin_port, tls_metrics_port=metrics_port,
                          experimental_binary=(pin['sha256'], pin['version']))
            report['initial_profiles'] = wait_profiles(metrics_port, True, 5)
            report['fallback_transfers'] = transfers(lab)
            lab.validate(4)
            time.sleep(3)  # Initial source failure must finish before opening the source.
            started = time.monotonic()
            origin = start_origin(Path(name), origin_port)
            report['recovered_profiles'] = wait_profiles(metrics_port, False, 45)
            report['recovery_seconds'] = time.monotonic() - started
            report['cache_files'] = cache_files(lab)
            report['recovered_transfers'] = transfers(lab)
            lab.validate(8)
            stop(origin)
            origin = None
            restart_core(lab)
            report['restart_profiles'] = wait_profiles(metrics_port, False, 10)
            if cache_files(lab) != report['cache_files']:
                raise RuntimeError('TLS cache changed while the source was down')
            report['restart_transfers'] = transfers(lab)
            lab.validate(12)
            report['source_down_restart_accepted'] = True
    finally:
        stop(origin)
        report['origin_closed'] = origin is None or origin.poll() is not None
        if lab is not None:
            lab.close()
            report['private_processes_closed'] = all(p.poll() is not None for p in lab.processes)
        else:
            report['private_processes_closed'] = False
        after = resources()
        report['resources_after'] = after
        report['oom_delta'] = {key: after['memory_events'][key] - before['memory_events'][key]
                               for key in ('oom', 'oom_kill')}
        report['accepted'] = (report.get('source_down_restart_accepted') is True
                              and report['origin_closed'] and report['private_processes_closed']
                              and after['pids_current'] == 1
                              and report['oom_delta'] == {'oom': 0, 'oom_kill': 0})
        write(args.output, report)
    return 0 if report['accepted'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    raise SystemExit(main(parser.parse_args()))
