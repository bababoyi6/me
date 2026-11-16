"""Bounded public TLS source fetch with a fixed core and synthetic local clients."""
import argparse
import os
from pathlib import Path
import time

from e2e_lab import FullLab
from paired_performance import resources, write
from release_pins import OLD_TLS_CACHE_SHA256, TLS_CANDIDATES
from tls_candidate_lab import candidate_binary
from tls_recovery_lab import cache_files, transfers


def main(args):
    before = resources()
    if (os.geteuid() != 0 or os.environ.get('GITHUB_ACTIONS') != 'true'
            or before['cpu_max'] != '100000 100000'
            or before['memory_max'] != str(512 * 1024**2)
            or before['swap_max'] != '0'):
        raise RuntimeError('bounded disposable CI cgroup required')
    binary, pin = candidate_binary(args.candidate, 'oldcache', os.environ['TDH_SOURCE_COMMIT'])
    if pin['sha256'] != OLD_TLS_CACHE_SHA256 or pin['version'] != TLS_CANDIDATES['oldcache'][3]:
        raise RuntimeError('fixed security2.2 candidate required')
    report = {'scope': 'public apple.com and gs.apple.com TLS source fetch; local synthetic DC and clients',
              'source_commit': os.environ['TDH_SOURCE_COMMIT'], 'binary_sha256': pin['sha256'],
              'resources_before': before, 'accepted': False}
    lab = None
    try:
        lab = FullLab(binary, experimental_binary=(pin['sha256'], pin['version']))
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                report['cache_files'] = cache_files(lab)
                break
            except (OSError, KeyError, ValueError, RuntimeError):
                time.sleep(.5)
        if 'cache_files' not in report:
            raise RuntimeError('public TLS profiles were not persisted before the deadline')
        report['transfers'] = transfers(lab)
        lab.validate(4)
        report['profiles_ready'] = True
    except Exception as error:
        report['error_type'] = type(error).__name__
        raise
    finally:
        if lab is not None:
            lab.close()
            report['private_processes_closed'] = all(p.poll() is not None for p in lab.processes)
        else:
            report['private_processes_closed'] = False
        after = resources()
        report['resources_after'] = after
        report['oom_delta'] = {key: after['memory_events'][key] - before['memory_events'][key]
                               for key in ('oom', 'oom_kill')}
        report['accepted'] = (report.get('profiles_ready') is True
                              and report['private_processes_closed']
                              and report['oom_delta'] == {'oom': 0, 'oom_kill': 0})
        write(args.output, report)
    return 0 if report['accepted'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    raise SystemExit(main(parser.parse_args()))
