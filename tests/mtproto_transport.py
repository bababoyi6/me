"""Synthetic MTProxy transport client and mock DC; no Telegram account or files.

Implements authenticated FakeTLS, obfuscated2 AES-CTR and padded intermediate
framing. Payloads in bulk experiments are synthetic, not MTProto user messages.
Requires cryptography only for explicitly invoked lab tests, never the manager.
"""
import hashlib
import hmac
import os
import socket
import ssl
import struct
import time

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from lab import exact

MAX_FRAME = 1024 * 1024


def aes(key, iv):
    return Cipher(algorithms.AES(key), modes.CTR(iv)).encryptor()


def client_nonce(secret, dc=2, random_bytes=None):
    raw = bytearray(random_bytes if random_bytes is not None else os.urandom(64))
    if len(raw) != 64 or len(secret) != 16:
        raise ValueError("invalid synthetic handshake dimensions")
    raw[0] = 0xaa
    raw[4:8] = b"\x01\x02\x03\x04"
    raw[56:60] = b"\xdd" * 4
    raw[60:62] = struct.pack("<h", dc)
    forward = bytes(raw[8:56])
    reverse = forward[::-1]
    encoder = aes(hashlib.sha256(forward[:32] + secret).digest(), forward[32:])
    decoder = aes(hashlib.sha256(reverse[:32] + secret).digest(), reverse[32:])
    encoded = encoder.update(bytes(raw))
    return bytes(raw[:56]) + encoded[56:], encoder, decoder


def dc_ciphers(nonce):
    if len(nonce) != 64:
        raise ValueError("invalid DC nonce length")
    forward, reverse = nonce[8:56], nonce[8:56][::-1]
    decoder = aes(forward[:32], forward[32:])
    decoded = decoder.update(nonce)
    if decoded[56:60] != b"\xdd" * 4:
        raise ValueError("unexpected DC transport")
    return aes(reverse[:32], reverse[32:]), decoder, struct.unpack("<h", decoded[60:62])[0]


class CryptoStream:
    def __init__(self, sock, encoder, decoder):
        self.sock, self.encoder, self.decoder = sock, encoder, decoder

    def sendall(self, data):
        self.sock.sendall(self.encoder.update(data))

    def recv(self, count):
        return self.decoder.update(self.sock.recv(count))


class FakeTlsStream:
    def __init__(self, sock):
        self.sock, self.pending = sock, bytearray()

    def sendall(self, data):
        for offset in range(0, len(data), 16384):
            chunk = data[offset:offset + 16384]
            self.sock.sendall(b"\x17\x03\x03" + struct.pack("!H", len(chunk)) + chunk)

    def recv(self, count):
        while not self.pending:
            header = exact(self.sock, 5)
            kind, major, minor, size = struct.unpack("!BBBH", header)
            if major != 3 or minor not in (1, 3) or size > 16640:
                raise ValueError("invalid FakeTLS record")
            body = exact(self.sock, size)
            if kind == 20 and body == b"\x01":
                continue
            if kind != 23:
                raise ValueError("unexpected FakeTLS record")
            self.pending.extend(body)
        data = bytes(self.pending[:count])
        del self.pending[:count]
        return data


class PacketStream:
    """Zero padding is valid for padded intermediate; never guesses pad lengths."""
    def __init__(self, stream):
        self.stream, self.pending = stream, bytearray()

    def sendall(self, data):
        if not 0 < len(data) <= MAX_FRAME:
            raise ValueError("bounded transport frame required")
        self.stream.sendall(struct.pack("<I", len(data)) + data)

    def frame(self):
        size = struct.unpack("<I", exact(self.stream, 4))[0]
        if not 0 < size <= MAX_FRAME:
            raise ValueError("invalid bounded transport frame")
        return exact(self.stream, size)

    def recv(self, count):
        if not self.pending:
            self.pending.extend(self.frame())
        data = bytes(self.pending[:count])
        del self.pending[:count]
        return data


def faketls_handshake(sock, domain, secret, proxy_v2=False, fragment=False):
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    client = ssl.create_default_context().wrap_bio(incoming, outgoing, server_hostname=domain)
    try:
        client.do_handshake()
    except ssl.SSLWantReadError:
        pass
    hello = bytearray(outgoing.read())
    hello[11:43] = bytes(32)
    digest = bytearray(hmac.digest(secret, hello, "sha256"))
    stamp = struct.pack("<I", int(time.time()))
    digest[28:32] = bytes(a ^ b for a, b in zip(digest[28:32], stamp))
    hello[11:43] = digest
    if proxy_v2:
        sock.sendall(b"\r\n\r\n\x00\r\nQUIT\n\x21\x11\x00\x0c" +
                     socket.inet_aton("203.0.113.20") + socket.inet_aton("127.0.0.1") + struct.pack("!HH", 50000, 443))
    if fragment:
        sock.sendall(hello[:17])
        time.sleep(.03)
        sock.sendall(hello[17:])
    else:
        sock.sendall(hello)
    flight = bytearray()
    while len(flight) < 65536:
        block = sock.recv(16384)
        if not block:
            break
        flight += block
        if len(flight) >= 43:
            zeroed = bytearray(flight)
            response_digest = bytes(zeroed[11:43])
            zeroed[11:43] = bytes(32)
            if hmac.compare_digest(response_digest, hmac.digest(secret, digest + zeroed, "sha256")):
                sock.sendall(b"\x14\x03\x03\x00\x01\x01")
                return FakeTlsStream(sock)
    raise ValueError("FakeTLS response HMAC verification failed")


def mtproxy_connect(sock, domain, secret, dc=2, proxy_v2=False, fragment=False):
    tls = faketls_handshake(sock, domain, secret, proxy_v2, fragment)
    nonce, encoder, decoder = client_nonce(secret, dc)
    tls.sendall(nonce)
    return PacketStream(CryptoStream(tls, encoder, decoder))


def req_pq(stream):
    """Public DH-initiation only; validates resPQ nonce, creates no user session."""
    nonce = os.urandom(16)
    body = struct.pack("<I", 0xbe7e8ef1) + nonce
    msg_id = (int(time.time() * 2**32) >> 2) << 2
    stream.sendall(bytes(8) + struct.pack("<QI", msg_id, len(body)) + body)
    response = stream.frame()
    if len(response) < 40 or response[:8] != bytes(8):
        raise ValueError("unexpected public DH response envelope")
    length = struct.unpack("<I", response[16:20])[0]
    if length < 20 or length > len(response) - 20:
        raise ValueError("invalid public DH response length")
    body = response[20:20 + length]
    if body[:4] != struct.pack("<I", 0x05162463) or body[4:20] != nonce:
        raise ValueError("public DH response nonce mismatch")
    return True
