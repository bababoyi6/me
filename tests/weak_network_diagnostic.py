"""Instrument genuine FullLab runs: capture EOF byte counts and sanitized core logs."""
import argparse
import copy
import json
import os
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
    args = parser.parse_args()
    os.umask(0o077)
    backend_cpu, controller_cpu = guard()
    os.sched_setaffinity(0, {controller_cpu})
    command(['tc', 'qdisc', 'add', 'dev', 'lo', 'root', 'netem', 'delay', '25ms', '5ms', 'loss', '0.1%',
             'rate', '100mbit', 'limit', '10000'])
    report = {'scope': 'diagnostic only; genuine HAProxy/Telemt/transport, synthetic DC; no production changes',
              'source_commit': os.environ['TDH_SOURCE_COMMIT'], 'complete': False, 'runs': [],
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
            for pair in range(1, 3):
                for kind, fast in (('balanced2', True), ('stable', True), ('stable', False)):
                    captured = {'errors': []}
                    ledger = root/f'ledger-{pair}-{kind}-{fast}'
                    startup = root/f'startup-{pair}-{kind}-{fast}.json'
                    class CapturedLab(FullLab):
                        def __init__(self, *values, **options):
                            super().__init__(*values, **options, fast=fast, artifact_registry=ledger, startup_report=startup)
                        def transfer(self, *values, **options):
                            try:
                                return super().transfer(*values, **options)
                            except Exception as error:
                                captured['errors'].append({'type': type(error).__name__,
                                    'phase': getattr(error, 'lab_phase', 'unknown'),
                                    'received_bytes': getattr(error, 'lab_received_bytes', None),
                                    'reason': sanitize_startup_log(str(error))[:256]})
                                raise
                        def close(self):
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
                    record['result'] = acceptance.run(binaries[kind], kind, offline_lab=True, backend_cpu=backend_cpu)
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
