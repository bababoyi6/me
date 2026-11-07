"""Native ARM64 fixed-release install/config/FakeTLS smoke, synthetic DC only."""
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from core_install import fresh_install, checksum
from e2e_lab import FullLab
from telemt_probe import request

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0 or platform.machine() != 'aarch64':
        raise RuntimeError('native aarch64 root environment required')
    lock = json.loads((ROOT/'docs/telemt-stable-lock.json').read_text())
    pin = lock['assets']['aarch64']
    report = {'scope': 'native ARM64 Linux; actual release/HAProxy/MTProto; synthetic DC and identities',
              'os': platform.freedesktop_os_release().get('PRETTY_NAME'), 'kernel': os.uname().release,
              'machine': platform.machine(), 'accepted': False}
    lab = None
    with tempfile.TemporaryDirectory(prefix='tdh-arm-compat-') as temp:
        root = Path(temp)
        root.chmod(0o755)
        binary = root/'telemt'
        try:
            # This maps only the account lookup; all archive, permission and
            # nonprivileged execution operations are the real installer code.
            with patch('core_install.pwd.getpwnam', return_value=SimpleNamespace(pw_uid=65534,pw_gid=65534)):
                report['fresh_install_version'] = fresh_install(args.archive,binary,pin['sha256'],
                                                                lock['tag'],pin['binary_sha256'],False)
            report['binary_sha256_matches'] = checksum(binary) == pin['binary_sha256']
            env = dict(os.environ,TDH_SOURCE_ONLY='1',TDH_BIN=str(binary),TDH_ARM_SOURCE=str(ROOT/'telemt-dual-hop.sh'))
            subprocess.run(['bash','-c','source "$TDH_ARM_SOURCE"; telemt_binary_is_expected'],
                           env=env,check=True,capture_output=True,timeout=10)
            report['production_arch_selection_verified'] = True
            lab = FullLab(binary,offline_lab=True)
            report['version'] = lab.version
            report['unauthenticated_api_status'] = request(lab.api,'GET','/v1/users',authenticated=False)[0]
            report['read_only_write_status'] = request(lab.api,'POST','/v1/users')[0]
            rows = []
            for domain in ('apple.com','gs.apple.com'):
                for direction in ('U','D'):
                    rows.append(lab.transfer(domain,direction,4096,1000000,persistent=True,fragment=True))
            # Bounded functional concurrency, not another throughput matrix.
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                futures = [pool.submit(lab.transfer,('apple.com','gs.apple.com')[i%2],
                                       ('U','D')[i%2],262144,12500000,persistent=True) for i in range(16)]
                rows += [future.result() for future in futures]
            lab.validate(len(rows))
            report.update(rows=rows,completed=len(rows),mock_dc_errors=lab.dc.errors,
                          config_unchanged=hashlib.sha256(lab.telemt_config.read_bytes()).hexdigest()==lab.config_hash)
            report['accepted'] = (report['binary_sha256_matches'] and report['config_unchanged']
                                  and report['unauthenticated_api_status'] in (401,403)
                                  and report['read_only_write_status']==403)
        except Exception as error:
            report['error_type'] = type(error).__name__
            raise
        finally:
            if lab is not None:
                lab.close()
            report['private_processes_closed'] = lab is None or all(p.poll() is not None for p in lab.processes)
            Path(args.output).write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'accepted':report['accepted'],'machine':report['machine']}))
    return 0 if report['accepted'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
