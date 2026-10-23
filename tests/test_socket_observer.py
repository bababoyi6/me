"""Validate kernel layouts, bounds and ownership without production traffic."""
import ast
from pathlib import Path
import errno
import socket
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import kernel_observer as observer


def attr(kind, body):
    size = 4 + len(body)
    return struct.pack('=HH', size, kind) + body + bytes((-size) % 4)


class SocketObserverTests(unittest.TestCase):
    def test_worker_delegates_process_scope_without_subprocess(self):
        tree=ast.parse(Path(__file__).with_name('high_rtt_worker.py').read_text())
        tree.body=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='observe']
        command=Mock(return_value=[{'role':'client','rb':425984}])
        env={'observe_sockets':command,
             'lab':SimpleNamespace(listen=32001,telemt=SimpleNamespace(pid=123))}
        exec(compile(tree,'socket-observer','exec'),env)
        rows=env['observe']()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['role'],'client')
        self.assertEqual(rows[0]['rb'],425984)
        command.assert_called_once_with(123,32001)

    def test_socket_memory_tcp_units_and_inode_ownership(self):
        body = bytearray(72)
        body[0] = socket.AF_INET
        struct.pack_into('!HH', body, 4, 32001, 50000)
        body[8:12] = socket.inet_aton('127.0.0.1')
        struct.pack_into('=I', body, 68, 99)
        info = bytearray(192)
        struct.pack_into('=I', info, 68, 100123)
        struct.pack_into('=Q', info, 176, 9876543210)
        struct.pack_into('=Q', info, 184, 4321000)
        body += attr(7, struct.pack('=9I', 128, 425984, 256, 425984, 0, 512, 0, 0, 3)) + attr(2, info)
        self.assertIsNone(observer.decode_socket(body, {100}, 32001))
        row = observer.decode_socket(body, {99}, 32001)
        self.assertEqual(row['role'], 'client')
        self.assertEqual(row['rb'], 425984)
        self.assertEqual(row['w_queued'], 512)
        self.assertEqual(row['rtt_ms'], 100.123)
        self.assertEqual(row['rwnd_limited_ms'], 9876543.21)
        self.assertEqual(row['sndbuf_limited_ms'], 4321)
        short = observer.decode_socket(body[:72] + attr(2, bytes(104)), {99}, 32001)
        self.assertNotIn('rwnd_limited_ms', short)
        self.assertNotIn('rb', short)

    def test_malformed_attributes_and_messages_rejected(self):
        for data in (b'1', struct.pack('=HH', 3, 1), struct.pack('=HH', 8, 1), attr(1, b'a') * 2):
            with self.subTest(data=data), self.assertRaises(observer.ObservationError):
                observer.attributes(data)
        for data in (b'1', observer.HEADER.pack(17, 20, 0, 7, 0),
                     observer.HEADER.pack(16, 20, 0, 8, 0), observer.HEADER.pack(16, 3, 0x10, 7, 0)):
            with self.subTest(data=data), self.assertRaises(observer.ObservationError):
                list(observer.messages(data, 7))

    def test_qdisc_nested_statistics_match_tc_fields(self):
        body = struct.pack('=B3xiIII', 0, 7, 0, 0, 0) + attr(1, b'netem\0')
        body += attr(7, attr(1, struct.pack('=QI', 1234567890123, 100)) +
                     attr(3, struct.pack('=5I', 1, 1500, 2, 3, 4)))
        with patch.object(socket, 'if_indextoname', return_value='wan-b'):
            row = observer.decode_qdisc(body)
        self.assertEqual(row, dict(dev='wan-b', kind='netem', bytes=1234567890123,
                                   packets=100, qlen=1, backlog=1500, drops=2, requeues=3, overlimits=4))
        with patch.object(socket, 'if_indextoname', return_value='wan-b'):
            with self.assertRaises(observer.ObservationError):
                observer.decode_qdisc(body[:20] + attr(1, b'fq\0'))

    def test_dump_completion_errors_sender_and_truncation(self):
        def packet(kind, body=b''):
            return observer.HEADER.pack(16 + len(body), kind, 0, 7, 456) + body
        for data, flags, sender, valid in (
                (packet(20, b'abcd') + packet(3, bytes(4)), 0, (0, 0), True),
                (packet(2, struct.pack('=i', -errno.EPERM)), 0, (0, 0), False),
                (packet(3, struct.pack('=i', -errno.EINTR)), 0, (0, 0), False),
                (packet(3), socket.MSG_TRUNC, (0, 0), False),
                (packet(3), 0, (123, 0), False)):
            channel = Mock()
            channel.__enter__ = Mock(return_value=channel)
            channel.__exit__ = Mock(return_value=False)
            channel.recvmsg.return_value = (data, [], flags, sender)
            with patch.object(socket, 'socket', return_value=channel), patch.object(observer.time, 'monotonic_ns', return_value=7):
                if valid:
                    self.assertEqual(observer.dump(4, 20, bytes(56)), [b'abcd'])
                else:
                    with self.assertRaises(observer.ObservationError):
                        observer.dump(4, 20, bytes(56))

    def test_namespace_allowlist_and_thread_rejection_before_open(self):
        with patch.object(observer.os, 'open') as opened:
            with self.assertRaises(observer.ObservationError):
                observer.qdiscs_in_namespace('../host')
            with patch.object(observer.threading, 'current_thread', return_value=object()):
                with self.assertRaises(observer.ObservationError):
                    observer.qdiscs_in_namespace('tdh-audit-a')
            opened.assert_not_called()

    def test_namespace_restore_runs_on_query_failure_and_restore_failure_is_fatal(self):
        for restore_fails in (False, True):
            libc = SimpleNamespace(setns=Mock(side_effect=[0, -1 if restore_fails else 0]))
            with patch.object(observer.ctypes, 'CDLL', return_value=libc), \
                    patch.object(observer.ctypes, 'get_errno', return_value=errno.EPERM), \
                    patch.object(observer.os, 'open', side_effect=[10, 11]), \
                    patch.object(observer.os, 'close') as closed, \
                    patch.object(observer, 'dump', side_effect=observer.ObservationError('query failed')):
                expected = RuntimeError if restore_fails else observer.ObservationError
                with self.assertRaises(expected):
                    observer.qdiscs_in_namespace('tdh-audit-a')
                self.assertEqual(libc.setns.call_args_list[0].args, (11, 0x40000000))
                self.assertEqual(libc.setns.call_args_list[1].args, (10, 0x40000000))
                self.assertEqual([call.args[0] for call in closed.call_args_list], [11, 10])

    def test_dump_bounds_and_deadline_fail_without_partial_results(self):
        packet = observer.HEADER.pack(20, 20, 0, 7, 0) + b'abcd'
        for limit, value in (('MAX_BYTES', 16), ('MAX_ROWS', 0)):
            channel = Mock()
            channel.__enter__ = Mock(return_value=channel)
            channel.__exit__ = Mock(return_value=False)
            channel.recvmsg.return_value = (packet, [], 0, (0, 0))
            with patch.object(socket, 'socket', return_value=channel), \
                    patch.object(observer.time, 'monotonic_ns', return_value=7), \
                    patch.object(observer, limit, value):
                with self.assertRaises(observer.ObservationError):
                    observer.dump(4, 20, bytes(56))
        with patch.object(socket, 'socket') as factory, \
                patch.object(observer.time, 'monotonic', side_effect=[0, 3]):
            with self.assertRaises(observer.ObservationError):
                observer.dump(4, 20, bytes(56))
            factory.return_value.__enter__.return_value.recvmsg.assert_not_called()

    def test_late_kernel_reply_is_not_accepted_as_a_valid_sample(self):
        channel=Mock()
        channel.__enter__=Mock(return_value=channel)
        channel.__exit__=Mock(return_value=False)
        channel.recvmsg.return_value=(observer.HEADER.pack(16,3,0,7,0),[],0,(0,0))
        with patch.object(socket,'socket',return_value=channel), \
                patch.object(observer.time,'monotonic_ns',return_value=7), \
                patch.object(observer.time,'monotonic',side_effect=[0,0,3]):
            with self.assertRaisesRegex(observer.ObservationError,'late netlink'):
                observer.dump(4,20,bytes(56))

    def test_whole_socket_observation_includes_descriptor_and_decode_time(self):
        with patch.object(observer.os,'stat',return_value=SimpleNamespace(st_ino=42)), \
                patch.object(observer,'socket_inodes',return_value={99}), \
                patch.object(observer,'dump',return_value=[]), \
                patch.object(observer.time,'monotonic',side_effect=[0,0,3]):
            with self.assertRaisesRegex(observer.ObservationError,'late socket observation'):
                observer.observe_sockets(123,32001)

    def test_late_qdisc_sample_is_rejected_after_namespace_restored(self):
        libc=SimpleNamespace(setns=Mock(return_value=0))
        with patch.object(observer.ctypes,'CDLL',return_value=libc), \
                patch.object(observer.os,'open',side_effect=[10,11]), \
                patch.object(observer.os,'close'), patch.object(observer,'dump',return_value=[]), \
                patch.object(observer.time,'monotonic',side_effect=[0,0,3]):
            with self.assertRaisesRegex(observer.ObservationError,'late qdisc observation'):
                observer.qdiscs_in_namespace('tdh-audit-a')
            self.assertEqual(libc.setns.call_args_list[-1].args,(10,0x40000000))
