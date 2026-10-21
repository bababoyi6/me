#!/usr/bin/env python3
"""Loopback-only SNI/PROXYv2 integrity tests and bounded HAProxy A/B benchmark.

No production addresses, keys, service changes or root privileges are used.
"""
import argparse
import concurrent.futures
import hashlib
import json
import math
import os
import signal
from pathlib import Path
import socket
import socketserver
import ssl
import statistics
import struct
import subprocess
import tempfile
import threading
import time

BLOCK = bytes(range(256)) * 256


def exact(sock, size):
    data = bytearray()
    while len(data) < size:
        block = sock.recv(min(size - len(data), 262144))
        if not block:
            raise RuntimeError("unexpected EOF")
        data.extend(block)
    return bytes(data)


def hello(domain):
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    client = ssl.create_default_context().wrap_bio(incoming, outgoing, server_hostname=domain)
    try:
        client.do_handshake()
    except ssl.SSLWantReadError:
        pass
    return outgoing.read()


def send_payload(sock, size, rate):
    start = time.monotonic()
    sent = 0
    digest = hashlib.sha256()
    while sent < size:
        block = BLOCK[:min(len(BLOCK), size - sent)]
        sock.sendall(block)
        digest.update(block)
        sent += len(block)
        if rate:
            time.sleep(max(0, sent / rate - (time.monotonic() - start)))
    return digest.digest()


def receive_payload(sock, size):
    digest = hashlib.sha256()
    remaining = size
    while remaining:
        block = sock.recv(min(262144, remaining))
        if not block:
            raise RuntimeError("truncated payload")
        digest.update(block)
        remaining -= len(block)
    return digest.digest()


class Backend(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, identity):
        self.identity = identity
        self.agent_up = True
        super().__init__(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.serve_forever, daemon=True).start()


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        sock = self.request
        sock.settimeout(12)
        header = exact(sock, 16)
        if header[:12] != b"\r\n\r\n\x00\r\nQUIT\n" or header[12] != 0x21:
            raise RuntimeError("missing PROXYv2")
        exact(sock, struct.unpack("!H", header[14:16])[0])
        record = exact(sock, 5)
        if record[0] != 22:
            raise RuntimeError("missing TLS ClientHello")
        exact(sock, struct.unpack("!H", record[3:5])[0])
        operation, size, rate = struct.unpack("!cQQ", exact(sock, 17))
        sock.sendall(self.server.identity)
        if operation == b"D":
            digest = send_payload(sock, size, rate)
            sock.sendall(digest)
        elif operation == b"U":
            sock.sendall(receive_payload(sock, size))
        else:
            raise RuntimeError("invalid operation")


class Agent(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, backend):
        self.backend = backend
        super().__init__(("127.0.0.1", 0), AgentHandler)
        threading.Thread(target=self.serve_forever, daemon=True).start()


class AgentHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.sendall(b"up\n" if self.server.backend.agent_up else b"down\n")


class Lab:
    def __init__(self, binary, splice=False, master=False):
        self.tmp = tempfile.TemporaryDirectory(prefix="tdh-lab-")
        self.root = Path(self.tmp.name)
        self.backends = [Backend(b"1"), Backend(b"2")]
        self.agents = [Agent(b) for b in self.backends]
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        pipes = "    maxpipes 256\n" if splice else ""
        option = "    option splice-auto\n" if splice else ""
        config = f"""global
    nbthread 1
    stats socket {self.root}/stats.sock level admin
{pipes}defaults
    mode tcp
    timeout connect 1s
    timeout client 15s
    timeout server 15s
frontend mtp_tls
    bind 127.0.0.1:{self.port}
    option tcp-smart-accept
    tcp-request inspect-delay 3s
    tcp-request content accept if {{ req.ssl_hello_type 1 }}
    use_backend telemt_a if {{ req.ssl_sni -i apple.com }}
    use_backend telemt_b if {{ req.ssl_sni -i gs.apple.com }}
    default_backend telemt_a
"""
        for name, order in (("a", [0, 1]), ("b", [1, 0])):
            config += f"backend telemt_{name}\n{option}"
            for pos, idx in enumerate(order):
                config += (f"    server vps{idx + 1} 127.0.0.1:{self.backends[idx].server_address[1]}"
                           f" check port {self.agents[idx].server_address[1]} inter 100ms fall 3 rise 2"
                           f" send-proxy-v2 agent-check agent-port {self.agents[idx].server_address[1]}"
                           f" agent-inter 100ms" + (" backup" if pos else "") + "\n")
        (self.root / "haproxy.cfg").write_text(config)
        self.process = subprocess.Popen([binary] + (["-W"] if master else []) + ["-db", "-f", str(self.root / "haproxy.cfg")],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 5
        while not (self.root / "stats.sock").exists():
            if self.process.poll() is not None:
                raise RuntimeError(self.process.stderr.read().decode())
            if time.monotonic() > deadline:
                raise RuntimeError("HAProxy startup timeout")
            time.sleep(.02)
        time.sleep(.3)

    def stat(self, command="show info"):
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(2)
            s.connect(str(self.root / "stats.sock"))
            s.sendall((command + "\n").encode())
            data = bytearray()
            while block := s.recv(8192):
                data.extend(block)
            return data.decode()

    def ticks(self):
        fields = Path(f"/proc/{self.process.pid}/stat").read_text().rsplit(")", 1)[1].split()
        return int(fields[11]) + int(fields[12])

    def transfer(self, domain, direction, size, rate, fragment=False):
        with socket.create_connection(("127.0.0.1", self.port), timeout=12) as s:
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            handshake = hello(domain)
            start = time.monotonic()
            if fragment:
                s.sendall(handshake[:30])
                time.sleep(.05)
                s.sendall(handshake[30:])
            else:
                s.sendall(handshake)
            s.sendall(struct.pack("!cQQ", direction.encode(), size, rate))
            identity = exact(s, 1).decode()
            latency = (time.monotonic() - start) * 1000
            if direction == "D":
                digest = receive_payload(s, size)
            else:
                digest = send_payload(s, size, rate)
            if exact(s, 32) != digest:
                raise RuntimeError("SHA256 integrity mismatch")
            return identity, latency

    def close(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.process.stderr.close()
        for server in self.agents + self.backends:
            server.shutdown()
            server.server_close()
        self.tmp.cleanup()


def integration(binary):
    for enabled in (False, True):
        lab = Lab(binary, enabled)
        try:
            for domain, expected in (("apple.com", "1"), ("gs.apple.com", "2")):
                identity, latency = lab.transfer(domain, "D", 1048576, 0, fragment=True)
                assert identity == expected
                assert latency < 1500, "ClientHello incurred unexpected inspection delay"
            lab.backends[0].agent_up = False
            deadline = time.monotonic() + 4
            while True:
                if lab.transfer("apple.com", "U", 65536, 0)[0] == "2":
                    break
                assert time.monotonic() < deadline, "agent down did not select backup"
                time.sleep(.1)
            lab.backends[0].agent_up = True
            deadline = time.monotonic() + 4
            while lab.transfer("apple.com", "D", 65536, 0)[0] != "1":
                assert time.monotonic() < deadline, "primary did not recover"
                time.sleep(.1)
        finally:
            lab.close()
    # Exercise master/worker reload while a verified stream is still active.
    lab = Lab(binary, master=True)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            active = pool.submit(lab.transfer, "apple.com", "D", 2097152, 1048576)
            time.sleep(.2)
            config = lab.root / "haproxy.cfg"
            text = config.read_text().replace("global\n", "global\n    maxpipes 256\n")
            for name in ("a", "b"):
                text = text.replace(f"backend telemt_{name}\n", f"backend telemt_{name}\n    option splice-auto\n")
            config.write_text(text)
            lab.process.send_signal(signal.SIGUSR2)
            time.sleep(.5)
            assert lab.transfer("gs.apple.com", "U", 65536, 0)[0] == "2"
            assert active.result(timeout=8)[0] == "1"
    finally:
        lab.close()


def benchmark(args):
    rows = []
    for streams in (1, 4):
        for direction in ("D", "U"):
            for turn in range(args.rounds):
                # Alternate A/B order to reduce temporal bias.
                for enabled in ((False, True) if turn % 2 == 0 else (True, False)):
                    lab = Lab(args.haproxy, enabled)
                    try:
                        rate = int(args.rate_mbps * 1000000 / 8 / streams)
                        size = int(rate * args.seconds)
                        before = lab.ticks()
                        start = time.monotonic()
                        with concurrent.futures.ThreadPoolExecutor(max_workers=streams) as pool:
                            futures = [pool.submit(lab.transfer, "apple.com", direction, size, rate)
                                       for _ in range(streams)]
                            time.sleep(min(.5, args.seconds / 2))
                            info = lab.stat()
                            results = [f.result(timeout=15) for f in futures]
                        elapsed = time.monotonic() - start
                        cpu_seconds = (lab.ticks() - before) / os.sysconf("SC_CLK_TCK")
                        pipes = dict(line.split(": ", 1) for line in info.splitlines() if ": " in line)
                        final_pipes = dict(line.split(": ", 1) for line in lab.stat().splitlines() if ": " in line)
                        rows.append({"splice": enabled, "streams": streams, "direction": direction,
                                     "round": turn + 1, "mbps": size * streams * 8 / elapsed / 1e6,
                                     "cpu_seconds_per_GiB": cpu_seconds / (size * streams / 2**30),
                                     "hello_ms_max": max(r[1] for r in results),
                                     "pipes_used_during_transfer": int(pipes.get("PipesUsed", 0)),
                                     "pipes_allocated_observed": max(
                                         int(pipes.get("PipesUsed", 0)) + int(pipes.get("PipesFree", 0)),
                                         int(final_pipes.get("PipesUsed", 0)) + int(final_pipes.get("PipesFree", 0))),
                                     "sha256_verified": True})
                    finally:
                        lab.close()
    summary = []
    for streams in (1, 4):
        for direction in ("D", "U"):
            for enabled in (False, True):
                selected = [r for r in rows if (r["streams"], r["direction"], r["splice"]) == (streams, direction, enabled)]
                item = {"streams": streams, "direction": direction, "splice": enabled}
                for metric in ("mbps", "cpu_seconds_per_GiB", "hello_ms_max"):
                    values = sorted(r[metric] for r in selected)
                    item[metric] = {"median": statistics.median(values), "p95": values[math.ceil(.95 * len(values)) - 1],
                                    "min": min(values), "max": max(values)}
                summary.append(item)
    output = {"scope": "loopback HAProxy only; no WireGuard, Telemt or Telegram measurement",
              "kernel": os.uname().release,
              "haproxy": subprocess.check_output([args.haproxy, "-v"], text=True).splitlines()[0],
              "rate_limit_mbps_aggregate": args.rate_mbps, "seconds": args.seconds, "rounds": args.rounds,
              "rows": rows, "summary": summary}
    Path(args.output).write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--haproxy", default="haproxy")
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--rate-mbps", type=int, choices=range(10, 1001), default=100)
    parser.add_argument("--seconds", type=int, choices=range(2, 6), default=3)
    parser.add_argument("--rounds", type=int, choices=range(3, 8), default=5)
    parser.add_argument("--output", default="lab-results.json")
    args = parser.parse_args()
    integration(args.haproxy)
    if args.benchmark:
        benchmark(args)
    print("SNI / fragmented ClientHello / PROXYv2 / SHA256 / agent failover passed", flush=True)
