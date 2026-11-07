"""Preserve the original fixture, collecting extra evidence only after an error."""
from pathlib import Path
import signal
import sys

from e2e_lab import FullLab, sanitize_startup_log
import paired_performance


def main():
    output = Path(sys.argv[sys.argv.index('--output')+1]).with_name('exceptions.json')
    observations = {'scope': 'original constructor/stdout/fixture unchanged; capture after exceptions only', 'errors': []}
    original = FullLab.transfer
    def transfer(self, *args, **kwargs):
        try:
            return original(self, *args, **kwargs)
        except Exception as error:
            observations['errors'].append({'version': self.version, 'type': type(error).__name__,
                'phase': getattr(error, 'lab_phase', 'unknown'),
                'received_bytes': getattr(error, 'lab_received_bytes', None),
                'reason': sanitize_startup_log(str(error))[:256],
                'dc_timeout_setting': self.dc.socket_timeout,
                'core_exit': self.telemt.poll(), 'mock_dc_errors_so_far': list(self.dc.errors)})
            raise
    FullLab.transfer = transfer
    try:
        return paired_performance.main()
    finally:
        FullLab.transfer = original
        paired_performance.write(output, observations)


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    raise SystemExit(main())
