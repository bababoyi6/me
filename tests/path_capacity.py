"""Receiver-local private TCP controls; reject mismatched measurement windows."""
import json
import math
import subprocess
import time

from network_lab import NAMES, command


def validate(payload, seconds=6):
    if payload.get('error'):
        raise ValueError('capacity probe error')
    end = payload.get('end', {})
    sent, received = end.get('sum_sent', {}), end.get('sum_received', {})
    for part in (sent, received):
        for key in ('seconds', 'bytes', 'bits_per_second'):
            value = part.get(key)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError('missing capacity measurements')
        if abs(part['seconds'] - seconds) > seconds * .10:
            raise ValueError('capacity measurement window mismatch')
        if not math.isclose(part['bits_per_second'], part['bytes'] * 8 / part['seconds'], rel_tol=.001):
            raise ValueError('capacity byte/rate mismatch')
    if abs(sent['bytes'] - received['bytes']) > max(sent['bytes'], received['bytes']) * .05:
        raise ValueError('capacity sender/receiver byte mismatch')
    # Both directions use a receiving client; do not silently fall back to a
    # sender rate when a remote receiver's reporting interval is inflated.
    if payload.get('start', {}).get('test_start', {}).get('reverse') != 1:
        raise ValueError('receiver-local capacity measurement required')
    intervals = [row['sum'] for row in payload.get('intervals', []) if row.get('sum', {}).get('omitted') is False]
    if len(intervals) < 5 or any(row.get('sender') is not False for row in intervals):
        raise ValueError('receiver interval evidence missing')
    if not math.isclose(sum(row['bytes'] for row in intervals), received['bytes'], rel_tol=.001):
        raise ValueError('receiver interval byte mismatch')
    return received['bits_per_second'] / 1e6


def controls(network, rate_mbps, rows=None):
    """Use the existing B server plus an owned A server, always reverse mode.

    Network.close owns both servers and namespaces, including exceptional exits.
    Both endpoints inherit the same affinity as the original controls. No host
    routes, socket defaults, kernel pacing, or prior raw results are changed.
    """
    server = subprocess.Popen(['ip', 'netns', 'exec', NAMES[0], 'iperf3',
        '-s', '-B', '10.77.1.1', '-p', '5201'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    network.processes.append(server)
    time.sleep(.4)
    if server.poll() is not None:
        raise RuntimeError('private capacity server startup failed')
    rows = [] if rows is None else rows
    for direction, receiver, sender in (('U', NAMES[2], '10.77.1.1'), ('D', NAMES[0], '10.77.1.2')):
        payload = json.loads(command('ip', 'netns', 'exec', receiver, 'iperf3', '-c', sender,
            '-t', '6', '-O', '2', '-b', str(rate_mbps) + 'M', '-P', '1', '-J', '-R',
            '--connect-timeout', '3000', timeout=25))
        row = {'direction': direction, 'receiver_local': True, 'raw': payload}
        rows.append(row)
        row['mbps'] = validate(payload)
    return rows
