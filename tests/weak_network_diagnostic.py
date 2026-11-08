"""Instrument genuine FullLab runs: capture EOF byte counts and sanitized core logs."""
import argparse
import copy
import json
import os
import platform
import re
from pathlib import Path
import signal
import tempfile
import time

import stable_acceptance as acceptance
from e2e_lab import FullLab, sanitize_startup_log
from paired_performance import guard, resources, write, command
from core_install import archive_payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stable-archive', required=True)
    parser.add_argument('--balanced2-archive', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--correlate', action='store_true', help='six weak-only 100-stream pairs with private per-connection debug logs')
    parser.add_argument('--retry-policy', action='store_true', help='test explicit 4s attempts / 9s total connect budget')
    args = parser.parse_args()
    os.umask(0o077)
    backend_cpu, controller_cpu = guard()
    os.sched_setaffinity(0, {controller_cpu})
    command(['tc', 'qdisc', 'add', 'dev', 'lo', 'root', 'netem', 'delay', '25ms', '5ms', 'loss', '0.1%',
             'rate', '100mbit', 'limit', '10000'])
    report = {'scope': 'diagnostic only; genuine HAProxy/Telemt/transport, synthetic DC; no production changes',
              'source_commit': os.environ['TDH_SOURCE_COMMIT'], 'complete': False, 'runs': [],
              'connect_policy': 'bounded-retry' if args.retry_policy else 'unchanged',
              'os': platform.freedesktop_os_release()['PRETTY_NAME'], 'kernel': os.uname().release,
              'cpu_model': next((s.split(':',1)[1].strip() for s in Path('/proc/cpuinfo').read_text().splitlines() if s.startswith('model name')), None),
              'resources_before': resources(), 'tcp_congestion_control': Path('/proc/sys/net/ipv4/tcp_congestion_control').read_text().strip()}
    output = Path(args.output)
    write(output, report)
    try:
        with tempfile.TemporaryDirectory(prefix='tdh-weak-diagnostic-') as directory:
            root = Path(directory)
            binaries = {}
            for kind, pins in acceptance.PINS.items():
                content, _ = archive_payload(getattr(args, kind+'_archive'), pins[0], pins[2].removeprefix('telemt '), pins[1], False)
                target = root/kind;target.write_bytes(content);target.chmod(0o755);binaries[kind] = target
            for pair in range(1, 7 if args.correlate else 3):
                order = (('balanced2', True), ('stable', True)) if pair % 2 else (('stable', True), ('balanced2', True))
                if not args.correlate:
                    order = (('balanced2', True), ('stable', True), ('stable', False))
                for kind, fast in order:
                    captured = {'errors': []}
                    ledger = root/f'ledger-{pair}-{kind}-{fast}'
                    startup = root/f'startup-{pair}-{kind}-{fast}.json'
                    class CapturedLab(FullLab):
                        def __init__(self, *values, **options):
                            super().__init__(*values, **options, fast=fast, artifact_registry=ledger, startup_report=startup,
                                             connection_logs=args.correlate, connect_policy='bounded-retry' if args.retry_policy else 'unchanged')
                        def transfer(self, *values, **options):
                            try:
                                return super().transfer(*values, **options)
                            except Exception as error:
                                captured['errors'].append({'type': type(error).__name__,
                                    'phase': getattr(error, 'lab_phase', 'unknown'),
                                    'received_bytes': getattr(error, 'lab_received_bytes', None),
                                    'client_port': getattr(error, 'lab_client_port', None),
                                    'elapsed_seconds': getattr(error, 'lab_elapsed_seconds', None),
                                    'reason': sanitize_startup_log(str(error))[:256]})
                                raise
                        def close(self):
                            if args.correlate and hasattr(self, 'root'):
                                for filename, key in (('startup.log', 'core_connection_log'), ('haproxy-errors.txt', 'haproxy_connection_log')):
                                    log = self.root/filename
                                    if log.is_file():
                                        # Read a bounded tail and export only failed synthetic flow events.
                                        with log.open('rb') as source:
                                            source.seek(max(0, log.stat().st_size-4*1024**2))
                                            lines = source.read(4*1024**2).decode(errors='replace').splitlines()
                                        for error in captured['errors']:
                                            port = error.get('client_port')
                                            pattern = re.compile(r'127\.0\.0\.1:'+str(port)+r'\b')
                                            error[key] = '\n'.join(sanitize_startup_log(line) for line in lines if pattern.search(line))[-12000:]
                            log = getattr(self, 'root', root)/'startup.log'
                            if log.is_file():
                                with log.open('rb') as stream:
                                    stream.seek(max(0, log.stat().st_size-32768))
                                    captured['sanitized_core_log_tail'] = sanitize_startup_log(stream.read(32768).decode(errors='replace'))
                            super().close()
                            captured['mock_dc_errors_after_close'] = copy.deepcopy(self.dc.errors) if self.dc else []
                    # Only the observer factory changes. All protocol, packets,
                    # release bytes and transfer code remain the maintained lab.
                    acceptance.FullLab = CapturedLab
                    record = {'round': pair, 'kind': kind, 'fast_mode': fast, 'observation': captured}
                    report['runs'].append(record)
                    record['result'] = acceptance.run(binaries[kind], kind, (100,) if args.correlate else (30, 50, 100),
                                                     offline_lab=True, backend_cpu=backend_cpu)
                    write(output, report)
                    print(json.dumps({'round': pair, 'kind': kind, 'fast': fast, 'errors': len(captured['errors'])}), flush=True)
            report['complete'] = True
    except BaseException as error:
        report['error_type'] = type(error).__name__
        raise
    finally:
        acceptance.FullLab = FullLab
        report['resources_after'] = resources()
        write(output, report)
    return 0


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    raise SystemExit(main())
