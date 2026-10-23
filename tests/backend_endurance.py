"""Bounded synthetic backend endurance; never measures real Telegram file speed.

Disposable root test server only. The raw TCP DC leg has 100 ms RTT / 100 Mbps;
this is deliberately not a WireGuard or production capacity benchmark.
"""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

from e2e_lab import FullLab
from network_lab import Network, NAMES, memory_available, summary


def worker(root, haproxy, duration):
    if subprocess.check_output(['ip', 'netns', 'identify'], text=True, timeout=3).strip() != NAMES[0]:
        raise RuntimeError('worker requires private client namespace')
    metadata = json.loads((root / 'auto-build.json').read_text())
    lab = FullLab(root / 'telemt-auto', haproxy, dc_endpoint=('198.51.100.2', 26000),
                  experimental_binary=(metadata['sha256'], metadata['version']))
    output = root / 'results/backend-endurance.json'
    report = {'scope': __doc__, 'complete': False, 'core_sha256': metadata['sha256'],
              'core_version': lab.version, 'duration_requested_seconds': duration,
              'raw_dc_leg_rtt_ms': 100, 'raw_dc_leg_rate_mbps': 100,
              'wireguard_in_path': False, 'payload_sha256_verified': 0,
              'slow_reader_pause_seconds': 3, 'slow_reader_streams_per_burst': 32,
              'slow_reader_bytes_per_stream': 2 * 1024**2,
              'samples': [], 'probes': [], 'transfers': []}
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=34)
    start = time.monotonic()
    initial_oom = oom_count()
    completed = 0
    futures = []
    try:
        for direction in ('D', 'U'):
            futures.append(pool.submit(lab.transfer, 'apple.com', direction, duration * 125000,
                                       125000, io_timeout=25, persistent=True))
        burst_done = [False, False]
        next_probe, next_print = 0, 0
        minimum_memory = memory_available()
        while True:
            elapsed = time.monotonic() - start
            if elapsed > duration + 120:
                raise TimeoutError('endurance deadline')
            available = memory_available()
            minimum_memory = min(minimum_memory, available)
            if available < 192 * 1024**2 or oom_count() != initial_oom:
                raise RuntimeError('memory safety stop')
            if lab.telemt.poll() is not None:
                raise RuntimeError('candidate exited')
            # Bursts at both ends expose growth across the sustained transfer.
            for index, threshold in enumerate((5, duration - 25)):
                if elapsed >= threshold and not burst_done[index]:
                    for stream in range(32):
                        futures.append(pool.submit(lab.transfer, 'gs.apple.com', 'D', 2 * 1024**2,
                                                   312500, io_timeout=25, persistent=True, read_pause=3))
                    burst_done[index] = True
            for future in list(futures):
                if future.done():
                    row = future.result()
                    report['transfers'].append(row)
                    completed += 1
                    futures.remove(future)
            if elapsed >= next_probe:
                row = lab.transfer('gs.apple.com', 'D', 1024, 1250000, io_timeout=5, persistent=True)
                report['probes'].append(row)
                completed += 1
                next_probe = elapsed + 5
            values = dict(line.split(':', 1) for line in Path(f'/proc/{lab.telemt.pid}/status').read_text().splitlines() if ':' in line)
            ticks = Path(f'/proc/{lab.telemt.pid}/stat').read_text().rsplit(')', 1)[1].split()
            sample = {'elapsed_seconds': elapsed, 'rss_bytes': int(values['VmRSS'].split()[0]) * 1024,
                      'fd_count': len(list(Path(f'/proc/{lab.telemt.pid}/fd').iterdir())),
                      'cpu_seconds': (int(ticks[11]) + int(ticks[12])) / os.sysconf('SC_CLK_TCK'),
                      'available_memory_bytes': available}
            report['samples'].append(sample)
            report.update(payload_sha256_verified=completed, minimum_available_memory_bytes=minimum_memory,
                          oom_kill_delta=oom_count() - initial_oom, elapsed_seconds=elapsed)
            output.write_text(json.dumps(report, indent=2) + '\n')
            if elapsed >= next_print:
                print(json.dumps({'elapsed_seconds': round(elapsed), 'completed_sha256': completed,
                                  'rss_bytes': sample['rss_bytes'], 'fd_count': sample['fd_count']}), flush=True)
                next_print = elapsed + 30
            if all(burst_done) and not futures:
                break
            time.sleep(1)
        lab.validate(completed)
        report.update(complete=True, config_unchanged=True,
                      probe_latency_ms=summary([r['elapsed_seconds'] * 1000 for r in report['probes']]),
                      maximum_rss_bytes=max(s['rss_bytes'] for s in report['samples']),
                      maximum_fd_count=max(s['fd_count'] for s in report['samples']),
                      steady_first_quarter_rss_median_bytes=statistics.median(s['rss_bytes'] for s in report['samples'] if duration * .1 <= s['elapsed_seconds'] <= duration * .25),
                      steady_last_quarter_rss_median_bytes=statistics.median(s['rss_bytes'] for s in report['samples'] if duration * .65 <= s['elapsed_seconds'] <= duration * .8))
    except BaseException as exc:
        report['failure_type'] = type(exc).__name__
        raise
    finally:
        lab.close()
        pool.shutdown(wait=True, cancel_futures=True)
        output.write_text(json.dumps(report, indent=2) + '\n')


def oom_count():
    return next(int(line.split()[1]) for line in Path('/proc/vmstat').read_text().splitlines() if line.startswith('oom_kill '))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--haproxy', required=True)
    parser.add_argument('--wg', default='wg')
    parser.add_argument('--duration', type=int, choices=(300, 600, 900), default=600)
    parser.add_argument('--inside', action='store_true')
    args = parser.parse_args()
    root = Path(args.root)
    if (os.geteuid() != 0 or root.resolve() != root or not root.name.startswith('tdh-audit-')
            or root.stat().st_uid != 0 or root.stat().st_mode & 0o077):
        parser.error('dedicated root-owned private directory required')
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.inside:
        worker(root, args.haproxy, args.duration)
        return
    net = Network(SimpleNamespace(root=str(root), wg=args.wg, start_iperf=False,
                                  rtt_ms=100, loss_percent=0, rate_mbps=100, mtu=1420))
    server = child = None
    with tempfile.TemporaryDirectory(prefix='tdh-audit-endurance-') as temporary:
        directory = Path(temporary)
        os.chown(directory, 65534, 65534)
        status = directory / 'dc.json'
        try:
            net.setup()
            server = subprocess.Popen(['ip', 'netns', 'exec', NAMES[2], sys.executable,
                                       str(Path(__file__).with_name('mock_dc_worker.py')), '--output', str(status)],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + 5
            while not status.exists():
                if server.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('mock DC readiness failure')
                time.sleep(.1)
            child = subprocess.Popen(['ip', 'netns', 'exec', NAMES[0], sys.executable, str(Path(__file__).resolve()),
                                      '--root', str(root), '--haproxy', args.haproxy, '--duration', str(args.duration), '--inside'],
                                     start_new_session=True)
            if child.wait(timeout=args.duration + 180):
                raise RuntimeError('endurance worker failed')
        finally:
            if child is not None and child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=35)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=5)
            if status.exists():
                (root / 'results/backend-endurance-dc.json').write_bytes(status.read_bytes())
            if server is not None:
                server.terminate()
                try:
                    server.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    server.kill(); server.wait(timeout=3)
            net.close()


if __name__ == '__main__':
    main()
