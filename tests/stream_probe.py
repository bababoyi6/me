#!/usr/bin/env python3
"""Private-namespace cross-check using the existing SHA256 streaming test code."""
import argparse
import hashlib
import json
import os
import socket
import socketserver
import struct
import time

from lab import Handler, exact, hello, receive_payload, send_payload


def main(args):
    if os.readlink("/proc/self/ns/net") == os.readlink("/proc/1/ns/net"):
        raise SystemExit("Refusing host network namespace; use the disposable network lab")
    if args.serve:
        with socketserver.ThreadingTCPServer(("0.0.0.0", 5202), Handler) as server:
            server.identity = b"1"
            server.daemon_threads = True
            server.serve_forever()
    else:
        size, rate = args.size, args.rate
        started = time.monotonic()
        with socket.create_connection((args.peer, 5202), timeout=8) as sock:
            sock.settimeout(8)
            sock.sendall(b"\r\n\r\n\x00\r\nQUIT\n\x21\x11\x00\x00" + hello("apple.com") + struct.pack("!cQQ", args.direction.encode(), size, rate))
            assert exact(sock, 1) == b"1"
            if args.direction == "D":
                digest = receive_payload(sock, size)
                assert digest == exact(sock, hashlib.sha256().digest_size)
            else:
                digest = send_payload(sock, size, rate)
                assert digest == exact(sock, hashlib.sha256().digest_size)
        elapsed = time.monotonic() - started
        print(json.dumps({"bytes": size, "elapsed_seconds": elapsed, "receiver_mbps": size * 8 / elapsed / 1e6,
                          "sha256_verified": True}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--peer", choices=("198.51.100.2", "10.77.1.2"))
    parser.add_argument("--direction", choices=("D", "U"), default="D")
    parser.add_argument("--size", type=int, default=18750000, metavar="BYTES")
    parser.add_argument("--rate", type=int, default=6250000, metavar="BYTES_PER_SECOND")
    arguments = parser.parse_args()
    if not 1 <= arguments.size <= 100000000 or not 1 <= arguments.rate <= 12500000:
        parser.error("size must be 1..100000000 bytes; rate must be 1..12500000 bytes/s")
    if not arguments.serve and arguments.peer is None:
        parser.error("--peer is required for a client")
    main(arguments)
