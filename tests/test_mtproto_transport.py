"""Lab transport bounds, fragmentation and an independent NIST CTR oracle.

NIST SP 800-38A F.5.5:
https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication800-38a.pdf
These exercise the test client, not Telegram account/message encryption.
"""
import io
import struct
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from lab import exact
from mtproto_transport import (MAX_FRAME, CryptoStream, FakeTlsStream, PacketStream,
                               aes, client_nonce, dc_ciphers)
from e2e_lab import FullLab


class Fragmented:
    def __init__(self, data=b"", chunk=3):
        self.incoming, self.outgoing = io.BytesIO(data), bytearray()
        self.chunk = chunk

    def recv(self, count):
        return self.incoming.read(min(count, self.chunk))

    def sendall(self, data):
        self.outgoing.extend(data)


class TransportTests(unittest.TestCase):
    def test_synthetic_transfer_cannot_use_public_dc_mode(self):
        lab = object.__new__(FullLab)
        lab.real_dc = True
        with patch("e2e_lab.socket.create_connection") as connection:
            with self.assertRaises(ValueError):
                lab.transfer("apple.com", "D", 1024, 1024)
            connection.assert_not_called()

    def test_experimental_policy_is_confined_before_any_resource_creation(self):
        options = ({"real_dc": True, "socket_library": "/synthetic.so"},
                   {"real_dc": True, "experimental_binary": ("0" * 64, "telemt 3.5.5+tdh.audit2")},
                   {"listen_endpoint": ("0.0.0.0", 443), "start_haproxy": False},
                   {"dc_endpoint": ("203.0.113.10", 443)}, {"socket_mode": "auto"})
        for option in options:
            with self.subTest(option=option), patch("e2e_lab.tempfile.TemporaryDirectory") as temporary:
                with self.assertRaises(ValueError):
                    FullLab("/never-read", **option)
                temporary.assert_not_called()

    def test_unknown_binary_is_rejected_before_any_process_or_temp_resource(self):
        with tempfile.TemporaryDirectory(prefix="tdh-untrusted-binary-") as folder:
            binary = Path(folder) / "synthetic-unknown"
            binary.write_bytes(b"SYNTHETIC_NOT_AN_EXECUTABLE")
            with patch("e2e_lab.tempfile.TemporaryDirectory") as temporary, patch("e2e_lab.subprocess.Popen") as process:
                with self.assertRaises(ValueError):
                    FullLab(binary)
                temporary.assert_not_called()
                process.assert_not_called()

    def test_nist_aes256_ctr_vector_and_partial_updates(self):
        key = bytes.fromhex("603deb1015ca71be2b73aef0857d7781" "1f352c073b6108d72d9810a30914dff4")
        iv = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff")
        plain = bytes.fromhex("6bc1bee22e409f96e93d7e117393172a" "ae2d8a571e03ac9c9eb76fac45af8e51"
                              "30c81c46a35ce411e5fbc1191a0a52ef" "f69f2445df4f9b17ad2b417be66c3710")
        expected = bytes.fromhex("601ec313775789a5b7a7f504bbf3d228" "f443e3ca4d62b59aca84e990cacaf5c5"
                                 "2b0930daa23de94ce87017ba2d84988d" "dfc9c58db67aada613c2dd08457941a6")
        encoder = aes(key, iv)
        actual = b"".join(encoder.update(plain[i:i + 3]) for i in range(0, len(plain), 3))
        self.assertEqual(actual, expected)
        self.assertEqual(aes(key, iv).update(expected), plain)

    def test_crypto_stream_keeps_counter_across_fragmented_reads(self):
        data = bytes(range(256)) * 3
        key, iv = bytes(range(32)), bytes(range(16))
        sock = Fragmented(aes(key, iv).update(data), chunk=7)
        stream = CryptoStream(sock, aes(key, iv), aes(key, iv))
        self.assertEqual(exact(stream, len(data)), data)
        stream.sendall(data[:17])
        stream.sendall(data[17:])
        self.assertEqual(bytes(sock.outgoing), aes(key, iv).update(data))

    def test_packet_stream_preserves_boundaries_and_partial_reads(self):
        sock = Fragmented(struct.pack("<I", 5) + b"hello" + struct.pack("<I", 3) + b"bye")
        stream = PacketStream(sock)
        self.assertEqual(stream.recv(2), b"he")
        self.assertEqual(exact(stream, 6), b"llobye")
        stream.sendall(b"test")
        self.assertEqual(bytes(sock.outgoing), b"\x04\0\0\0test")

    def test_packet_stream_rejects_unbounded_frames_before_body_read(self):
        for size in (0, MAX_FRAME + 1, 0xffffffff):
            with self.subTest(size=size), self.assertRaises(ValueError):
                PacketStream(Fragmented(struct.pack("<I", size))).frame()
        for data in (b"", bytes(MAX_FRAME + 1)):
            with self.assertRaises(ValueError):
                PacketStream(Fragmented()).sendall(data)

    def test_truncated_packet_is_an_error(self):
        with self.assertRaises(RuntimeError):
            PacketStream(Fragmented(b"\x08\0\0\0short")).frame()

    def test_tls_stream_handles_ccs_and_partial_records(self):
        record = b"\x14\x03\x03\x00\x01\x01\x17\x03\x03\x00\x05hello"
        stream = FakeTlsStream(Fragmented(record, chunk=1))
        self.assertEqual(exact(stream, 5), b"hello")
        stream.sendall(b"x" * 20000)
        self.assertEqual(len(stream.sock.outgoing), 20010)
        self.assertEqual(stream.sock.outgoing[16389:16394], b"\x17\x03\x03\x0e\x20")

    def test_tls_stream_rejects_bad_type_version_or_oversized_record(self):
        for header in (b"\x17\x03\x03\xff\xff", b"\x17\x02\x03\0\0", b"\x16\x03\x03\0\0"):
            with self.subTest(header=header), self.assertRaises(ValueError):
                FakeTlsStream(Fragmented(header)).recv(1)

    def test_invalid_nonce_is_rejected(self):
        for data in (b"", bytes(63), bytes(64)):
            with self.assertRaises(ValueError):
                dc_ciphers(data)
        with self.assertRaises(ValueError):
            client_nonce(bytes(15))
        with self.assertRaises(ValueError):
            client_nonce(bytes(16), random_bytes=bytes(63))
