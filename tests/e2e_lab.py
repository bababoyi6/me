#!/usr/bin/env python3
"""Bounded loopback Client -> HAProxy -> Telemt -> mock DC transport experiment.

Synthetic identities and payloads only; does not measure Telegram file speed.
Requires an explicitly supplied, previously verified official Telemt binary.
"""
import argparse
import concurrent.futures
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import socketserver
import stat
import struct
import subprocess
import tempfile
import threading
import time

from lab import exact, receive_payload, send_payload
from lab_config import generate_telemt
from mtproto_transport import CryptoStream, PacketStream, dc_ciphers, mtproxy_connect, req_pq
from telemt_probe import SECRETS, port, request
from lab_artifacts import register_artifact
from lab_files import file_sha256, link_private_binary

# Extracted x86_64 binaries from the checksum-verified official GNU archives.
BINARIES = {
    "cd34e20e9f5c4457579479e321eba2e65496712f0e794df6acb5b63d2ee01dcd": "telemt 3.5.5",
    "feb1d8779de0b86c6c43d1b871020ec94b11fad9fec5612b07b278edfd2181ff": "telemt 3.5.14",
}


@contextmanager
def close_on_error(resource):
    """Place after an executor in with: abort sockets before joining workers."""
    try:
        yield
    except BaseException:
        getattr(resource, 'abort', resource.close)()
        raise


class ObservedReader:
    def __init__(self, stream):
        self.stream, self.bytes = stream, 0

    def recv(self, count):
        block = self.stream.recv(count)
        self.bytes += len(block)
        return block


class DcHandler(socketserver.BaseRequestHandler):
    def handle(self):
        phase = "nonce"
        observed, expected = None, None
        try:
            self.request.settimeout(self.server.socket_timeout)
            # Telemt's upstream health checks connect and close without a nonce.
            first = self.request.recv(1)
            if not first:
                with self.server.error_lock:
                    self.server.connect_only_checks += 1
                return
            encoder, decoder, dc = dc_ciphers(first + exact(self.request, 63))
            if dc != 2:
                raise ValueError("unexpected synthetic DC index")
            stream = PacketStream(CryptoStream(self.request, encoder, decoder))
            phase = "control"
            operation, size, rate = struct.unpack("!cQQ", exact(stream, 17))
            persistent = operation in (b"d", b"u")
            operation = operation.upper()
            if not 0 < size <= 128 * 1024**2 or not 0 < rate <= 125000000:
                raise ValueError("bounded synthetic payload required")
            phase = "payload"
            if operation == b"D":
                digest = send_payload(stream, size, rate)
            elif operation == b"U":
                observed, expected = ObservedReader(stream), size
                digest = receive_payload(observed, size)
            else:
                raise ValueError("unexpected synthetic operation")
            phase = "digest"
            if persistent and operation == b"D":
                # No trailing DC bytes/EOF may rescue a buffered download tail.
                phase = "ack"
                if exact(stream, 4) != b"ACK!":
                    raise ValueError("invalid synthetic completion acknowledgement")
            stream.sendall(digest)
            if persistent and operation == b"U":
                phase = "ack"
                if exact(stream, 4) != b"ACK!":
                    raise ValueError("invalid synthetic completion acknowledgement")
                stream.sendall(b"OK")
            with self.server.error_lock:
                self.server.completed += 1
        except Exception as exc:
            with self.server.error_lock:
                details = {"phase": phase, "type": type(exc).__name__}
                if observed is not None:
                    details.update(upload_received_bytes=observed.bytes, upload_expected_bytes=expected)
                self.server.errors.append(details)


class MockDc(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True
    # Accommodate 16-stream bursts plus probes without a five-slot lab bottleneck.
    request_queue_size = 64

    def __init__(self, bind="127.0.0.1", listen=0, *, socket_timeout=8, receive_buffer=0):
        if socket_timeout not in (8, 25):
            raise ValueError("mock DC socket timeout must be bounded")
        if receive_buffer not in (0,262144):
            raise ValueError('bounded mock DC receive buffer required')
        self.socket_timeout = socket_timeout
        self.errors, self.error_lock = [], threading.Lock()
        self.connect_only_checks, self.completed = 0, 0
        super().__init__((bind, listen), DcHandler, bind_and_activate=False)
        try:
            if receive_buffer:
                self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,receive_buffer)
            self.receive_buffer_actual=self.socket.getsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF)
            self.server_bind()
            self.server_activate()
        except BaseException:
            self.server_close()
            raise
        threading.Thread(target=self.serve_forever, daemon=True).start()


class FullLab:
    def __init__(self, binary, haproxy="haproxy", fast=True, real_dc=False, splice=False,
                 dc_endpoint=None, socket_library=None, socket_mode="pass", small_copy=False,
                 listen_endpoint=None, start_haproxy=True, experimental_binary=None,artifact_registry=None,
                 shared_binary_root=None):
        if listen_endpoint is not None and (listen_endpoint not in (("10.77.1.2", 24431), ("10.77.2.2", 24431))
                                            or real_dc or start_haproxy):
            raise ValueError("private namespace backend requires a fixed WG bind and no local frontend")
        if dc_endpoint is not None and dc_endpoint not in (("198.51.100.2", 26000),
                                                          ("198.18.0.1", 26000), ("198.18.0.5", 26000)):
            raise ValueError("external mock DC must be the fixed private namespace endpoint")
        if real_dc and (dc_endpoint or socket_library or experimental_binary or shared_binary_root is not None):
            raise ValueError("experimental socket policies are forbidden for public DC probes")
        if socket_mode not in ("pass", "auto", "1m", "4m", "client-auto", "dc-auto", "auto-lowat") or (socket_mode != "pass" and socket_library is None):
            raise ValueError("explicit lab socket library required for experimental policy")
        binary = Path(binary)
        info = binary.stat()
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 64 * 1024**2:
            raise ValueError("bounded regular Telemt binary required")
        binary_hash = file_sha256(binary)
        expected = BINARIES.get(binary_hash)
        if experimental_binary is not None:
            checksum, version = experimental_binary
            if checksum != binary_hash or version not in ("telemt 3.5.5", "telemt 3.5.5+tdh.audit1", "telemt 3.5.5+tdh.audit2", "telemt 3.5.5+tdh.fair1", "telemt 3.5.5+tdh.queue1", "telemt 3.5.5+tdh.balanced1"):
                raise ValueError("explicit experimental build checksum/version mismatch")
            expected = version
        if expected is None:
            raise ValueError("lab requires a pinned official x86_64 GNU binary")
        self.temp = tempfile.TemporaryDirectory(prefix="tdh-e2e-",dir='/tmp' if artifact_registry is not None else None)
        self.root = Path(self.temp.name)
        self.processes, self.dc = [], None
        self.real_dc = real_dc
        try:
            self.uid = 65534 if os.geteuid() == 0 else os.geteuid()
            self.gid = 65534 if os.geteuid() == 0 else os.getegid()
            if os.geteuid() == 0:
                os.chown(self.root, self.uid, self.gid)
            if artifact_registry is not None:
                register_artifact(artifact_registry,self.root)
            target = self.root / "telemt"
            self.binary_staging = 'private-shared-inode' if shared_binary_root is not None else 'private-copy'
            if shared_binary_root is not None:
                link_private_binary(binary, target, shared_binary_root, binary_hash)
            else:
                shutil.copyfile(binary, target)
                target.chmod(0o755)
            if file_sha256(target) != binary_hash:
                raise ValueError("Telemt binary changed during private copy")
            self.version = subprocess.check_output([str(target), "--version"], text=True, timeout=5,
                                                   **self.identity()).strip()
            if self.version != expected:
                raise ValueError("unexpected binary version")
            self.listen, self.api, self.front = listen_endpoint[1] if listen_endpoint else port(), port(), port()
            config_dir, work = self.root / "config", self.root / "work"
            bind_ip = listen_endpoint[0] if listen_endpoint else "127.0.0.1"
            entry_ip = bind_ip.rsplit(".", 1)[0] + ".1" if listen_endpoint else "127.0.0.1"
            config = generate_telemt(config_dir, work, self.listen, self.api, bind_ip, entry_ip)
            text = config.read_text()
            if not fast:
                text = text.replace("fast_mode = true", "fast_mode = false")
            if small_copy:
                if real_dc:
                    raise ValueError("experimental copy policy is confined to synthetic DC tests")
                text = text.replace("[general]\n", "[general]\ndirect_relay_copy_buf_c2s_bytes = 4096\ndirect_relay_copy_buf_s2c_bytes = 16384\n")
            if not real_dc:
                if dc_endpoint is None:
                    self.dc = MockDc()
                    dc_endpoint = self.dc.server_address
                text += '\n[dc_overrides]\n' + "".join(
                    f'"{dc}" = ["{dc_endpoint[0]}:{dc_endpoint[1]}"]\n' for dc in range(1, 6))
            config.write_text(text)
            self.telemt_config = config
            self.config_hash = hashlib.sha256(config.read_bytes()).hexdigest()
            if os.geteuid() == 0:
                for path in (config_dir, work, *work.rglob("*"), *config_dir.rglob("*")):
                    os.chown(path, self.uid, self.gid)
            environment = dict(os.environ)
            if socket_library is not None:
                library = self.root / "socket-policy.so"
                shutil.copyfile(socket_library, library)
                library.chmod(0o644)
                environment.update(LD_PRELOAD=str(library), TDH_LAB_SOCKET_MODE=socket_mode,
                                   TDH_LAB_CLIENT_PORT=str(self.listen))
            self.telemt = subprocess.Popen([str(target), str(config)], cwd=work / "telemt", env=environment, **self.identity(),
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.processes.append(self.telemt)
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline and self.telemt.poll() is None:
                try:
                    if request(self.api, "GET", "/v1/health")[0] == 200:
                        break
                except (OSError, ValueError):
                    pass
                time.sleep(.1)
            else:
                raise RuntimeError("Telemt startup failed; raw logs withheld")
            if not start_haproxy:
                return
            config = self.root / "haproxy.cfg"
            pipes = "    maxpipes 128\n" if splice else ""
            forwarding = "    option splice-auto\n" if splice else ""
            config.write_text(f'''global
    nbthread 1
{pipes}defaults
    mode tcp
    timeout connect 3s
    timeout client 20s
    timeout server 20s
frontend mtp_tls
    bind 127.0.0.1:{self.front}
    tcp-request inspect-delay 3s
    tcp-request content accept if {{ req.ssl_hello_type 1 }}
    use_backend telemt if {{ req.ssl_sni -i apple.com gs.apple.com }}
    default_backend telemt
backend telemt
{forwarding}    server lab_backend 127.0.0.1:{self.listen} send-proxy-v2
''')
            config.chmod(0o600)
            if os.geteuid() == 0:
                os.chown(config, self.uid, self.gid)
            haproxy_source = shutil.which(haproxy)
            if haproxy_source is None:
                raise ValueError("explicit HAProxy executable required")
            haproxy_target = self.root / "haproxy"
            shutil.copyfile(haproxy_source, haproxy_target)
            haproxy_target.chmod(0o755)
            haproxy_environment = dict(os.environ)
            for folder in os.environ.get("LD_LIBRARY_PATH", "").split(":"):
                library = Path(folder) / "liblua5.3.so.0" if folder else None
                if library is not None and library.is_file():
                    shutil.copyfile(library, self.root / library.name)
                    (self.root / library.name).chmod(0o644)
            haproxy_environment["LD_LIBRARY_PATH"] = str(self.root)
            error_file = self.root / "haproxy-errors.txt"
            with error_file.open("wb") as errors:
                self.haproxy = subprocess.Popen([str(haproxy_target), "-db", "-f", str(config)], cwd=self.root, env=haproxy_environment, **self.identity(),
                                                stdout=subprocess.DEVNULL, stderr=errors)
            self.processes.append(self.haproxy)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and self.haproxy.poll() is None:
                try:
                    with socket.create_connection(("127.0.0.1", self.front), timeout=.1):
                        break
                except OSError:
                    time.sleep(.05)
            else:
                # This test configuration contains no credentials; Telemt logs remain withheld.
                raise RuntimeError("private HAProxy startup failed: " + error_file.read_text()[:2000])
        except BaseException:
            self.close()
            raise

    def identity(self):
        return {"user": self.uid, "group": self.gid, "extra_groups": []} if os.geteuid() == 0 else {}

    def transfer(self, domain, direction, size, rate, direct=False, fragment=False, io_timeout=8, persistent=False, read_pause=0, receive_buffer=0, send_buffer=0):
        if self.real_dc:
            raise ValueError("synthetic file operations are confined to the mock DC")
        if not 2 <= io_timeout <= 30:
            raise ValueError("bounded lab IO timeout required")
        if not 0 <= read_pause <= 5 or (read_pause and direction != "D"):
            raise ValueError("bounded download reader pause required")
        if receive_buffer not in (0, 32768):
            raise ValueError("bounded lab receive buffer required")
        if send_buffer not in (0,262144):
            raise ValueError('bounded lab send buffer required')
        started = time.monotonic()
        phase, observed = "connect", None
        try:
            with socket.create_connection(("127.0.0.1", self.listen if direct else self.front), timeout=8) as sock:
                if receive_buffer:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, receive_buffer)
                if send_buffer:
                    sock.setsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF,send_buffer)
                sock.settimeout(io_timeout)
                phase = "handshake"
                stream = mtproxy_connect(sock, domain, SECRETS[domain], proxy_v2=direct, fragment=fragment)
                ready = time.monotonic()
                phase = "control"
                stream.sendall(struct.pack("!cQQ", (direction.lower() if persistent else direction).encode(), size, rate))
                if read_pause:
                    time.sleep(read_pause)
                phase = "payload"
                if direction == "D":
                    observed = ObservedReader(stream)
                    digest = receive_payload(observed, size)
                else:
                    digest = send_payload(stream, size, rate)
                if persistent and direction == "D":
                    phase = "ack"
                    stream.sendall(b"ACK!")
                phase = "digest"
                if exact(stream, 32) != digest:
                    raise RuntimeError("synthetic payload SHA256 mismatch")
                if persistent and direction == "U":
                    phase = "ack"
                    stream.sendall(b"ACK!")
                    if exact(stream, 2) != b"OK":
                        raise RuntimeError("synthetic acknowledgement mismatch")
        except Exception as exc:
            exc.lab_phase = phase
            exc.lab_received_bytes = observed.bytes if observed else None
            raise
        return {"direction": direction, "domain_index": list(SECRETS).index(domain) + 1,
                "path": "telemt" if direct else "haproxy-telemt", "bytes": size,
                "elapsed_seconds": time.monotonic() - started, "handshake_ms": (ready - started) * 1000,
                "sha256_verified": True, "persistent_dc": persistent}

    def real_probe(self, dc=2):
        if not self.real_dc:
            raise RuntimeError("public DC probe requires explicit mode")
        started = time.monotonic()
        with socket.create_connection(("127.0.0.1", self.front), timeout=8) as sock:
            sock.settimeout(8)
            stream = mtproxy_connect(sock, "apple.com", SECRETS["apple.com"], dc=dc)
            valid = req_pq(stream)
        return {"dc_index": dc, "req_pq_nonce_verified": valid, "elapsed_seconds": time.monotonic() - started}

    def validate(self, completed):
        if self.dc:
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                with self.dc.error_lock:
                    if self.dc.errors:
                        raise RuntimeError("mock DC protocol error; raw packets withheld")
                    if self.dc.completed == completed:
                        break
                time.sleep(.01)
            else:
                raise RuntimeError("mock DC completion count mismatch")
        if hashlib.sha256(self.telemt_config.read_bytes()).hexdigest() != self.config_hash:
            raise RuntimeError("test configuration unexpectedly changed")
        if any(process.poll() is not None for process in self.processes):
            raise RuntimeError("private proxy unexpectedly exited")

    def abort(self):
        # Safety thresholds must stop allocation before graceful shutdown waits.
        # These Popen handles belong only to this private synthetic fixture.
        for process in reversed(self.processes):
            if process.poll() is None:
                process.kill()
        self.close()

    def close(self):
        for process in reversed(self.processes):
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if self.dc is not None:
            self.dc.shutdown()
            self.dc.server_close()
        self.temp.cleanup()


def main(args):
    lab = FullLab(args.telemt, args.haproxy, fast=not args.no_fast, real_dc=args.real_dc, splice=args.splice)
    try:
        if args.real_dc:
            rows = [lab.real_probe(args.dc) for _ in range(3)]
            scope = "public req_pq handshake through real proxy layers; no account/files or throughput claim"
        else:
            rows = [lab.transfer(domain, direction, 131072, 2500000, direct=direct, fragment=True)
                    for direct in (True, False) for domain in SECRETS for direction in ("D", "U")]
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                rows += list(executor.map(lambda i: lab.transfer(list(SECRETS)[i % 2], "D" if i % 2 else "U", 1048576, 1250000), range(4)))
            scope = "loopback authenticated FakeTLS/PROXYv2/AES-CTR/padded transport with synthetic mock DC, not Telegram files"
        lab.validate(len(rows))
        result = {"scope": scope, "kernel": os.uname().release, "telemt": lab.version, "rows": rows,
                  "fast_mode": not args.no_fast,
                  "splice": args.splice,
                  "haproxy": subprocess.check_output([args.haproxy, "-v"], text=True, timeout=3).splitlines()[0],
                  "config_unchanged": True}
        if lab.dc:
            result["mock_dc_completed"] = lab.dc.completed
            result["mock_dc_connect_only_checks"] = lab.dc.connect_only_checks
        Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps({"scope": scope, "completed": len(rows), "telemt": lab.version}), flush=True)
    finally:
        lab.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--telemt", required=True)
    parser.add_argument("--haproxy", default="haproxy")
    parser.add_argument("--output", required=True)
    parser.add_argument("--real-dc", action="store_true", help="explicit public DH-initiation only, never account login")
    parser.add_argument("--no-fast", action="store_true", help="lab-only comparison; never edits installed configuration")
    parser.add_argument("--splice", action="store_true", help="lab-only HAProxy splice-auto with bounded pipes")
    parser.add_argument("--dc", type=int, choices=range(1, 6), default=2)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    main(args)
