"""Bounded, read-only Linux netlink observations for the private lab only.

Linux UAPI: inet_diag.h, sock_diag.h, tcp.h, rtnetlink.h, gen_stats.h.
No external command per sample. Missing optional TCP fields remain absent.
"""
import ctypes
import errno
import os
import socket
import struct
import threading
import time

HEADER = struct.Struct('=IHHII')
MAX_BYTES = 4 * 1024**2
MAX_ROWS = 4096
NAMES = ('tdh-audit-a', 'tdh-audit-wan', 'tdh-audit-b')


class ObservationError(OSError):
    """An incomplete or malformed observation must never become zero counters."""


def attributes(data):
    result = {}
    offset = 0
    while offset < len(data):
        if len(data) - offset < 4:
            raise ObservationError('short attribute header')
        length, kind = struct.unpack_from('=HH', data, offset)
        if length < 4 or offset + length > len(data):
            raise ObservationError('invalid attribute length')
        kind &= 0x3fff
        if kind in result:
            raise ObservationError('duplicate attribute')
        result[kind] = data[offset + 4:offset + length]
        offset += (length + 3) & ~3
    if offset != len(data):
        raise ObservationError('short attribute padding')
    return result


def messages(data, sequence):
    offset = 0
    while offset < len(data):
        if len(data) - offset < HEADER.size:
            raise ObservationError('short netlink header')
        length, kind, flags, seq, _ = HEADER.unpack_from(data, offset)
        if length < HEADER.size or offset + length > len(data) or seq != sequence:
            raise ObservationError('invalid netlink length or sequence')
        if flags & 0x10:  # NLM_F_DUMP_INTR
            raise ObservationError('interrupted netlink dump')
        yield kind, data[offset + HEADER.size:offset + length]
        offset += (length + 3) & ~3
    if offset != len(data):
        raise ObservationError('short netlink padding')


def dump(protocol, kind, payload, timeout=2, response_kind=None):
    """Require kernel sender, matching sequence, complete dump and hard bounds."""
    sequence = time.monotonic_ns() & 0xffffffff
    deadline = time.monotonic() + timeout
    total, rows = 0, []
    with socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, protocol) as channel:
        channel.bind((0, 0))
        channel.settimeout(timeout)
        channel.sendto(HEADER.pack(16 + len(payload), kind, 0x301, sequence, 0) + payload, (0, 0))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ObservationError('netlink deadline')
            channel.settimeout(remaining)
            data, _, flags, sender = channel.recvmsg(65536)
            if time.monotonic() > deadline:
                raise ObservationError('late netlink reply')
            total += len(data)
            if flags & socket.MSG_TRUNC or sender != (0, 0) or not data or total > MAX_BYTES:
                raise ObservationError('truncated, foreign or oversized netlink dump')
            for message_kind, body in list(messages(data, sequence)):
                if message_kind == 2:  # NLMSG_ERROR
                    if len(body) < 4:
                        raise ObservationError('short netlink error')
                    error = struct.unpack_from('=i', body)[0]
                    if error:
                        raise ObservationError(abs(error), os.strerror(abs(error)))
                elif message_kind == 3:  # NLMSG_DONE may carry an error code
                    if body and (len(body) < 4 or struct.unpack_from('=i', body)[0]):
                        raise ObservationError('netlink dump failed')
                    if time.monotonic() > deadline:
                        raise ObservationError('late netlink completion')
                    return rows
                elif message_kind == (kind if response_kind is None else response_kind):
                    rows.append(body)
                    if len(rows) > MAX_ROWS:
                        raise ObservationError('too many netlink records')
                else:
                    raise ObservationError('unexpected netlink message type')


def socket_inodes(pid, deadline):
    result = set()
    with os.scandir(f'/proc/{int(pid)}/fd') as descriptors:
        for index, entry in enumerate(descriptors):
            if index >= MAX_ROWS:
                raise ObservationError('too many process descriptors')
            try:
                target = os.readlink(entry.path)
            except FileNotFoundError:  # descriptor closed during scan
                continue
            if target.startswith('socket:[') and target.endswith(']'):
                result.add(int(target[8:-1]))
            if time.monotonic() > deadline:
                raise ObservationError('late process descriptor scan')
    return result


def decode_socket(body, inodes, listen):
    if len(body) < 72 or body[0] != socket.AF_INET:
        raise ObservationError('short or unsupported socket diagnostic')
    inode = struct.unpack_from('=I', body, 68)[0]
    if inode not in inodes:
        return None
    sport, dport = struct.unpack_from('!HH', body, 4)
    src, dst = socket.inet_ntoa(body[8:12]), socket.inet_ntoa(body[24:28])
    role = ('client' if (src, sport) == ('127.0.0.1', listen) else
            'dc' if (dst, dport) == ('198.51.100.2', 26000) else None)
    if role is None:
        return None
    attrs = attributes(body[72:])
    item = {'role': role}
    memory = attrs.get(7, b'')  # INET_DIAG_SKMEMINFO
    for key, index in (('r_allocated', 0), ('rb', 1), ('w_allocated', 2),
                       ('tb', 3), ('w_queued', 5), ('socket_drops', 8)):
        if len(memory) >= (index + 1) * 4:
            item[key] = struct.unpack_from('=I', memory, index * 4)[0]
    info = attrs.get(2, b'')  # INET_DIAG_INFO / struct tcp_info
    for key, offset, fmt, divisor in (
            ('rtt_ms', 68, 'I', 1000), ('total_retrans', 100, 'I', 1),
            ('bytes_acked', 120, 'Q', 1), ('bytes_received', 128, 'Q', 1),
            ('notsent_bytes', 144, 'I', 1), ('busy_ms', 168, 'Q', 1000),
            ('rwnd_limited_ms', 176, 'Q', 1000), ('sndbuf_limited_ms', 184, 'Q', 1000)):
        if len(info) >= offset + struct.calcsize('=' + fmt):
            value = struct.unpack_from('=' + fmt, info, offset)[0]
            item[key] = value / divisor if divisor != 1 else value
    return item


def observe_sockets(pid, listen):
    # Only the isolated client namespace: bounded dump, then process inode ownership.
    deadline = time.monotonic() + 2
    if os.stat('/proc/self/ns/net').st_ino != os.stat('/run/netns/tdh-audit-a').st_ino:
        raise ObservationError('socket observation outside private client namespace')
    inodes = socket_inodes(pid, deadline)
    request = struct.pack('=BBBBI', socket.AF_INET, socket.IPPROTO_TCP, 66, 0, 2)
    request += bytes(40) + struct.pack('=II', 0xffffffff, 0xffffffff)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ObservationError('late socket observation setup')
    rows = dump(4, 20, request, timeout=remaining)
    result = [item for body in rows if (item := decode_socket(body, inodes, listen)) is not None]
    if time.monotonic() > deadline:
        raise ObservationError('late socket observation')
    return result


def decode_qdisc(body):
    if len(body) < 20:
        raise ObservationError('short tcmsg')
    _, index, _, _, _ = struct.unpack_from('=B3xiIII', body)
    attrs = attributes(body[20:])
    kind = attrs.get(1, b'').rstrip(b'\0').decode('ascii')
    if kind not in ('fq', 'netem'):
        return None
    result = {'dev': socket.if_indextoname(index), 'kind': kind}
    stats = attributes(attrs[7]) if 7 in attrs else {}
    if 1 in stats:
        if len(stats[1]) < 12:
            raise ObservationError('short basic statistics')
        result['bytes'], result['packets'] = struct.unpack_from('=QI', stats[1])
    if 3 in stats:
        if len(stats[3]) < 20:
            raise ObservationError('short queue statistics')
        result.update(zip(('qlen', 'backlog', 'drops', 'requeues', 'overlimits'),
                          struct.unpack_from('=IIIII', stats[3])))
    # Old kernels may only provide the legacy tc_stats structure.
    if 3 in attrs and not stats:
        if len(attrs[3]) < 36:
            raise ObservationError('short legacy statistics')
        values = struct.unpack_from('=QIIIIIII', attrs[3])
        result.update(zip(('bytes', 'packets', 'drops', 'overlimits', 'bps', 'pps', 'qlen', 'backlog'), values))
    required = ('bytes', 'packets', 'drops', 'overlimits', 'backlog', 'qlen')
    if any(key not in result for key in required):
        raise ObservationError('incomplete qdisc counters')
    return result


def qdiscs_in_namespace(name):
    """Temporarily enter only an allowlisted lab netns; restore this thread always."""
    if name not in NAMES or threading.current_thread() is not threading.main_thread():
        raise ObservationError('namespace or thread outside lab scope')
    deadline = time.monotonic() + 2
    libc = ctypes.CDLL(None, use_errno=True)
    libc.setns.argtypes = (ctypes.c_int, ctypes.c_int)
    libc.setns.restype = ctypes.c_int

    def enter(fd):
        if libc.setns(fd, 0x40000000):
            error = ctypes.get_errno() or errno.EPERM
            raise ObservationError(error, os.strerror(error))

    original = os.open('/proc/self/ns/net', os.O_RDONLY | os.O_CLOEXEC)
    target = None
    try:
        target = os.open('/run/netns/' + name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        enter(target)
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ObservationError('late qdisc observation setup')
            bodies = dump(0, 38, struct.pack('=B3xiIII', socket.AF_UNSPEC, 0, 0, 0, 0),
                          response_kind=36, timeout=remaining)
            result = [row for body in bodies if (row := decode_qdisc(body)) is not None]
        finally:
            # Restoration failure is fatal, not a recoverable missing sample.
            try:
                enter(original)
            except OSError as exc:
                raise RuntimeError('failed to restore original network namespace') from exc
    finally:
        if target is not None:
            os.close(target)
        os.close(original)
    if time.monotonic() > deadline:
        raise ObservationError('late qdisc observation')
    return result
