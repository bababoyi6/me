"""Fixed HAProxy runtime operations; no shell, untrusted commands or peer logins."""
import csv
import io
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import tempfile
import time


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


HEALTH_BACKEND = b'''backend telemt_health
    option tcp-check
    # Health-agent ready line: up followed by LF.
    tcp-check expect rbinary ^75700[aA]$
    default-server inter 2s fastinter 500ms fall 3 rise 2
    server vps1 10.77.1.2:19101 check
    server vps2 10.77.2.2:19101 check

'''


def tracked_health_config(original):
    """Replace the four shipped server directives in their original sections."""
    if re.search(rb'(?m)^[ \t]*backend[ \t]+telemt_health(?:\s|$)', original):
        raise MaintenanceError('发现已有健康后端；请检查运行状态或使用入口健康回退')
    updated = original
    for route, servers in ((b'a', (1, 2)), (b'b', (2, 1))):
        header = b'backend telemt_' + route + b'\n'
        old_lines, new_lines = [], []
        for position, index in enumerate(servers):
            endpoint = f'    server vps{index} 10.77.{index}.2:24431'.encode('ascii')
            backup = b' backup' if position else b''
            old_lines.append(endpoint + b' check port 19101 inter 2s fall 3 rise 2 send-proxy-v2'
                             b' agent-check agent-port 19101 agent-inter 2s' + backup + b'\n')
            new_lines.append(endpoint + f' track telemt_health/vps{index} send-proxy-v2'.encode('ascii')
                             + backup + b'\n')
        old_block = header + b''.join(old_lines)
        if updated.count(old_block) != 1:
            raise MaintenanceError('入口主备配置与已发布模板不符；请先检查自定义配置')
        updated = updated.replace(old_block, (HEALTH_BACKEND if route == b'a' else b'')
                                  + header + b''.join(new_lines), 1)
    return updated


def runtime_status(request):
    rows = csv.DictReader(io.StringIO(request('show stat').lstrip('# ')))
    statuses = {}
    for row in rows:
        key = row.get('pxname'), row.get('svname')
        if key[0] in ('telemt_a', 'telemt_b', 'telemt_health') and key[1] in ('vps1', 'vps2'):
            if key in statuses:
                raise MaintenanceError('HAProxy 返回重复的后端状态')
            statuses[key] = (row.get('status') or '', row.get('check_status') or '')
    return statuses


def runtime_layout(statuses, tracked):
    backends = ('telemt_a', 'telemt_b', 'telemt_health') if tracked else ('telemt_a', 'telemt_b')
    return statuses.keys() == {(name, server) for name in backends for server in ('vps1', 'vps2')}


def runtime_ready(statuses, tracked):
    return runtime_layout(statuses, tracked) and all(
        state == 'UP' and (name != 'telemt_health' or check == 'L7OK')
        for (name, _), (state, check) in statuses.items())


def checked_config(path):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_nlink != 1 or info.st_mode & 0o022 or info.st_size > 1024**2):
        raise MaintenanceError('入口配置或备份的权限、类型或大小不符合要求')
    return path.read_bytes(), info


def publish(path, content, info, *, validate=False):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.haproxy-', delete=False) as stream:
        temp = Path(stream.name)
        try:
            os.fchown(stream.fileno(), info.st_uid, info.st_gid)
            os.fchmod(stream.fileno(), stat.S_IMODE(info.st_mode))
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            if validate:
                subprocess.run(('/usr/sbin/haproxy', '-c', '-f', temp), check=True, timeout=8,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            os.replace(temp, path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temp.unlink(missing_ok=True)


def reload_entry():
    subprocess.run(('/usr/bin/systemctl', 'reload', 'telemt-dual-hop-haproxy.service'), check=True,
                   timeout=12, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_runtime(request, tracked, *, require_ready):
    deadline = time.monotonic() + 15
    verify = runtime_ready if require_ready else runtime_layout
    while time.monotonic() < deadline:
        try:
            if verify(runtime_status(request), tracked):
                return
        except (OSError, MaintenanceError):
            pass
        time.sleep(.25)
    raise MaintenanceError('HAProxy 重载后未确认目标运行状态')


def migrate_health(path, rollback, request=exchange, reload=reload_entry):
    current, info = checked_config(path)
    previous = path.with_name(path.name + '.health-previous')
    have_backup = previous.exists() or previous.is_symlink()
    if have_backup:
        old, _ = checked_config(previous)
        new = tracked_health_config(old)
        if current not in (old, new):
            raise MaintenanceError('入口配置与健康迁移备份不一致；请先检查配置漂移')
    else:
        if rollback:
            raise MaintenanceError('没有可用的入口健康迁移备份')
        old, new = current, tracked_health_config(current)

    target = old if rollback else new
    live = runtime_status(request)
    live_tracked = any(name == 'telemt_health' for name, _ in live)
    if not runtime_layout(live, live_tracked) or (live_tracked and not have_backup):
        raise MaintenanceError('磁盘配置和 HAProxy 运行状态无法核对；请先运行 a status')
    verify = runtime_layout if rollback else runtime_ready
    if current == target and verify(live, not rollback):
        return 'unchanged'
    if any(state.startswith(('DRAIN', 'MAINT')) for state, _ in live.values()):
        raise MaintenanceError('检测到后端维护状态；请先结束排空或维护')
    if not rollback and not runtime_ready(live, live_tracked):
        raise MaintenanceError('两条线路的四条路径未全部 UP；请先恢复后端或结束排空')
    if not have_backup:
        publish(previous, old, info)
    if current != target:
        publish(path, target, info, validate=True)
    try:
        reload()
        wait_runtime(request, not rollback, require_ready=not rollback)
    except (Exception, KeyboardInterrupt):
        try:
            publish(path, new if live_tracked else old, info)
            reload()
            wait_runtime(request, live_tracked, require_ready=False)
        except (Exception, KeyboardInterrupt):
            raise MaintenanceError('重载与自动恢复未能确认；已保留入口健康备份，请检查 a status') from None
        raise MaintenanceError('新状态未能确认；已恢复操作前的配置和路由状态') from None
    return 'rolled-back' if rollback else 'applied'


if __name__ == '__main__':
    try:
        if len(sys.argv) != 3:
            raise ValueError('exact arguments required')
        action, value = sys.argv[1:]
        if action in ('health-refresh', 'health-rollback'):
            print('入口健康配置：'+migrate_health(Path(value), action == 'health-rollback'))
        else:
            print('后端维护状态：'+maintain(action, value))
    except Exception as error:
        detail = str(error) if isinstance(error, MaintenanceError) else '无法完成 HAProxy 操作；请运行 a status 和 a diagnose，检查两条线路后重试原命令'
        print('入口维护失败：'+detail, file=sys.stderr)
        raise SystemExit(1)
