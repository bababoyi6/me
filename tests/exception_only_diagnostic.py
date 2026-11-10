"""Preserve the original fixture, collecting extra evidence only after an error."""
from pathlib import Path
import json
import ssl
import cryptography
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
        # Read environment details AFTER traffic, without perturbing the failure.
        observations['environment'] = {'openssl': ssl.OPENSSL_VERSION,
                                        'python_cryptography': cryptography.__version__}
        observations['environment']['tcp_sysctl'] = {name: Path('/proc/sys/net/ipv4', name).read_text().strip()
            for name in ('tcp_congestion_control', 'tcp_rmem', 'tcp_wmem', 'tcp_tw_reuse', 'tcp_timestamps', 'ip_local_port_range')}
        observations['environment']['cpu_flags'] = next((line.split(':',1)[1].strip()
            for line in Path('/proc/cpuinfo').read_text().splitlines() if line.startswith('flags')), '')
        paired_performance.write(output, observations)


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    raise SystemExit(main())
