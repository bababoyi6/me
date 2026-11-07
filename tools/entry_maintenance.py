"""Fixed HAProxy runtime operations; no shell, untrusted commands or peer logins."""
import csv
import io
import socket
import sys


def exchange(command):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(3)
        connection.connect('/run/telemt-dual-hop/haproxy.sock')
        connection.sendall((command+'\n').encode('ascii'))
        connection.shutdown(socket.SHUT_WR)
        output = bytearray()
        while True:
            part = connection.recv(16384)
            if not part:
                return output.decode('ascii')
            output += part
            if len(output) > 262144:
                raise RuntimeError('HAProxy reply exceeds limit')


def maintain(action, index, request=exchange):
    if action not in ('drain', 'resume') or index not in ('1', '2'):
        raise ValueError('fixed action and backend index required')
    if action == 'drain':
        rows = list(csv.DictReader(io.StringIO(request('show stat').lstrip('# '))))
        peer = 'vps'+('2' if index == '1' else '1')
        for backend in ('telemt_a', 'telemt_b'):
            matches = [row for row in rows if row.get('pxname') == backend and row.get('svname') == peer]
            if len(matches) != 1 or matches[0].get('status') != 'UP':
                raise RuntimeError('peer is not UP on both lines; refusing drain')
    state = 'drain' if action == 'drain' else 'ready'
    for backend in ('telemt_a', 'telemt_b'):
        reply = request(f'set server {backend}/vps{index} state {state}')
        if reply.strip():
            raise RuntimeError('HAProxy rejected state change; inspect both lines')
    return state


if __name__ == '__main__':
    try:
        if len(sys.argv) != 3:
            raise ValueError('exact arguments required')
        print('后端维护状态：'+maintain(sys.argv[1], sys.argv[2]))
    except Exception as error:
        print('入口维护失败：'+type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
