"""Fixed HAProxy runtime operations; no shell, untrusted commands or peer logins."""
import csv
import io
import socket
import sys


class MaintenanceError(RuntimeError):
    """Only fixed, non-sensitive operator messages may cross the CLI boundary."""


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
                raise MaintenanceError('HAProxy 状态响应过大；请运行 a diagnose，未继续操作')


def maintain(action, index, request=exchange):
    if action not in ('drain', 'resume') or index not in ('1', '2'):
        raise ValueError('fixed action and backend index required')
    if action == 'drain':
        rows = list(csv.DictReader(io.StringIO(request('show stat').lstrip('# '))))
        peer = 'vps'+('2' if index == '1' else '1')
        for backend in ('telemt_a', 'telemt_b'):
            matches = [row for row in rows if row.get('pxname') == backend and row.get('svname') == peer]
            if len(matches) != 1 or matches[0].get('status') != 'UP':
                raise MaintenanceError('备用后端未在两条线路均为 UP，已拒绝排空；请先运行 a diagnose')
    state = 'drain' if action == 'drain' else 'ready'
    for backend in ('telemt_a', 'telemt_b'):
        reply = request(f'set server {backend}/vps{index} state {state}')
        if reply.strip():
            raise MaintenanceError('HAProxy 拒绝状态变更；两条线路可能只有一条已变更，请运行 a status 检查并重试原命令')
    return state


if __name__ == '__main__':
    try:
        if len(sys.argv) != 3:
            raise ValueError('exact arguments required')
        print('后端维护状态：'+maintain(sys.argv[1], sys.argv[2]))
    except Exception as error:
        detail = str(error) if isinstance(error, MaintenanceError) else '无法完成 HAProxy 操作；请运行 a status 和 a diagnose，检查两条线路后重试原命令'
        print('入口维护失败：'+detail, file=sys.stderr)
        raise SystemExit(1)
