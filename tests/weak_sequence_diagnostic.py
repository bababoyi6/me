"""Repeat the failing full sequence with observation only; no retries erase failures."""
import copy
import os
from pathlib import Path
import signal
import sys
import tempfile

import paired_performance
import stable_acceptance
from e2e_lab import FullLab, sanitize_startup_log


def main():
    output = Path(sys.argv[sys.argv.index('--output')+1])
    observations = {'scope': 'observer only; genuine binaries and maintained full paired sequence', 'runs': []}
    with tempfile.TemporaryDirectory(prefix='tdh-observer-') as directory:
        private = Path(directory)
        class ObservedLab(FullLab):
            def __init__(self, *args, **kwargs):
                self.observation = {'ordinal': len(observations['runs'])+1, 'errors': []}
                observations['runs'].append(self.observation)
                self.observation['experimental_version'] = kwargs.get('experimental_binary', (None, 'official'))[1] if kwargs.get('experimental_binary') else 'official'
                number = self.observation['ordinal']
                super().__init__(*args, **kwargs, artifact_registry=private/f'ledger-{number}', startup_report=private/f'startup-{number}.json')
            def transfer(self, *args, **kwargs):
                try:
                    return super().transfer(*args, **kwargs)
                except Exception as error:
                    self.observation['errors'].append({'type': type(error).__name__,
                        'phase': getattr(error, 'lab_phase', 'unknown'),
                        'received_bytes': getattr(error, 'lab_received_bytes', None),
                        'reason': sanitize_startup_log(str(error))[:256]})
                    raise
            def close(self):
                if hasattr(self, 'telemt'):
                    self.observation['core_exit_before_cleanup'] = self.telemt.poll()
                log = getattr(self, 'root', private)/'startup.log'
                if log.is_file():
                    with log.open('rb') as source:
                        source.seek(max(0, log.stat().st_size-32768))
                        tail = sanitize_startup_log(source.read(32768).decode(errors='replace'))
                        self.observation['core_log_tail'] = '\n'.join(line for line in tail.splitlines() if 'tg://proxy' not in line and 'https://t.me/proxy' not in line)
                super().close()
                self.observation['mock_dc_errors_after_close'] = copy.deepcopy(self.dc.errors) if self.dc else []
                paired_performance.write(output.with_name('observations.json'), observations)
        stable_acceptance.FullLab = ObservedLab
        try:
            return paired_performance.main()
        finally:
            stable_acceptance.FullLab = FullLab


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    raise SystemExit(main())
