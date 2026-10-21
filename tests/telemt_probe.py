#!/usr/bin/env python3
"""Disposable-host compatibility probe; verified binaries, synthetic identities only.

Run explicitly as root on an otherwise unused test host. Children run as nobody,
bind only loopback, and are removed on exit. This does not measure MTProto files.
"""
import argparse
import concurrent.futures
import hashlib
import hmac
import http.client
import json
import math
import os
from pathlib import Path
import shutil
import signal
import socket
import ssl
import statistics
import struct
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "synthetic-telemt-probe-token"
SECRETS = {"apple.com": bytes.fromhex("10" * 16), "gs.apple.com": bytes.fromhex("20" * 16)}


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def request(api_port, method, path, authenticated=True):
    connection = http.client.HTTPConnection("127.0.0.1", api_port, timeout=2)
    try:
        headers = {"Authorization": "Bearer " + TOKEN} if authenticated else {}
        connection.request(method, path, body=b"{}" if method != "GET" else None, headers=headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read(262144))
    finally:
        connection.close()


def handshake(listen_port, domain):
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    client = ssl.create_default_context().wrap_bio(incoming, outgoing, server_hostname=domain)
    try:
        client.do_handshake()
    except ssl.SSLWantReadError:
        pass
    hello = bytearray(outgoing.read())
    hello[11:43] = bytes(32)
    digest = bytearray(hmac.digest(SECRETS[domain], hello, "sha256"))
    stamp = struct.pack("<I", int(time.time()))
    digest[28:32] = bytes(a ^ b for a, b in zip(digest[28:32], stamp))
    hello[11:43] = digest
    proxy = b"\r\n\r\n\x00\r\nQUIT\n\x21\x11\x00\x0c" + socket.inet_aton("203.0.113.20") + socket.inet_aton("127.0.0.1") + struct.pack("!HH", 50000, listen_port)
    started = time.monotonic()
    with socket.create_connection(("127.0.0.1", listen_port), timeout=3) as sock:
        sock.settimeout(3)
        sock.sendall(proxy + hello)
        received = bytearray()
        while len(received) < 65536:
            block = sock.recv(16384)
            if not block:
                break
            received += block
            if len(received) < 43:
                continue
            candidate = bytearray(received)
            actual = bytes(candidate[11:43])
            candidate[11:43] = bytes(32)
            if hmac.compare_digest(actual, hmac.digest(SECRETS[domain], digest + candidate, "sha256")):
                return (time.monotonic() - started) * 1000
    raise RuntimeError("authenticated FakeTLS response verification failed")


def run_version(root, version):
    binary = root / ("telemt-" + version) / "telemt"
    with tempfile.TemporaryDirectory(prefix="tdh-audit-telemt-") as directory:
        work = Path(directory)
        os.chown(work, 65534, 65534)
        target = work / "telemt"
        shutil.copyfile(binary, target)
        target.chmod(0o755)
        if subprocess.check_output([str(target), "--version"], text=True, timeout=5,
                                   user=65534, group=65534, extra_groups=[]).strip() != "telemt " + version:
            raise RuntimeError("binary version mismatch")
        config_dir, work_dir = work / "config", work / "work"
        config_dir.mkdir()
        work_dir.mkdir()
        listen_port, api_port = port(), port()
        env = dict(os.environ, TDH_SOURCE_ONLY="1", TDH_CONFIG_DIR=str(config_dir), TDH_WORK_ROOT=str(work_dir))
        generated = subprocess.run(["bash", "-c", '''
set -Eeuo pipefail
source "$1"
install() {
  local args=()
  while (( $# )); do case $1 in -o|-g) shift 2;; *) args+=("$1"); shift;; esac; done
  command install "${args[@]}"
}
chown() { :; }
BACKEND_MAX_CONNECTIONS=2048 BACKEND_BUFFER_BUDGET_BYTES=268435456
ENTRY_PUBLIC_IP=203.0.113.10 ENTRY_WG_IP=127.0.0.1
API_TOKEN=synthetic-telemt-probe-token
SECRET_A=10101010101010101010101010101010 SECRET_B=20202020202020202020202020202020
write_telemt_config 127.0.0.1 "$2" "$3" 443
''', "probe", str(ROOT / "telemt-dual-hop.sh"), str(listen_port), str(api_port)],
                                   env=env, capture_output=True, timeout=10)
        if generated.returncode:
            raise RuntimeError("configuration generation failed; details withheld")
        for path in (config_dir, work_dir, *work_dir.rglob("*"), *config_dir.rglob("*")):
            os.chown(path, 65534, 65534)
        config = config_dir / "telemt.toml"
        config_digest = hashlib.sha256(config.read_bytes()).hexdigest()
        # Never print logs: Telemt may print the synthetic links/credentials.
        process = subprocess.Popen([str(target), str(config)], cwd=work_dir / "telemt", user=65534,
                                   group=65534, extra_groups=[], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            started = time.monotonic()
            ready = False
            while time.monotonic() - started < 35 and process.poll() is None:
                try:
                    ready = request(api_port, "GET", "/v1/health")[0] == 200
                except (OSError, ValueError):
                    pass
                if ready:
                    break
                time.sleep(.2)
            if not ready:
                return {"version": version, "ready": False, "exit": process.poll(), "scope": "startup only; raw logs withheld"}
            startup = time.monotonic() - started
            unauthorized, _ = request(api_port, "GET", "/v1/users", authenticated=False)
            readonly, _ = request(api_port, "POST", "/v1/users")
            status, users = request(api_port, "GET", "/v1/users")
            assert status == 200 and unauthorized in (401, 403) and readonly == 403
            links_ok = all(any(link.endswith(domain.encode().hex()) and "server=203.0.113.10&port=443" in link
                               for link in user["links"]["tls"])
                           for user, domain in zip(sorted(users["data"], key=lambda u: u["username"]), SECRETS))
            assert len(users["data"]) == 2 and links_ok
            latencies = [handshake(listen_port, domain) for _ in range(5) for domain in SECRETS]
            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
                concurrent_latencies = list(pool.map(lambda d: handshake(listen_port, d), list(SECRETS) * 8))
            def summarize(values):
                ordered = sorted(values)
                return {"median": statistics.median(ordered), "p95": ordered[math.ceil(.95 * len(ordered))-1],
                        "min": min(ordered), "max": max(ordered)}
            memory = next(int(line.split()[1])*1024 for line in Path(f"/proc/{process.pid}/status").read_text().splitlines() if line.startswith("VmRSS:"))
            return {"version": version, "ready": True, "startup_seconds": startup,
                    "unauthenticated_api_status": unauthorized, "read_only_write_status": readonly,
                    "synthetic_links_match": links_ok, "config_unchanged": hashlib.sha256(config.read_bytes()).hexdigest() == config_digest,
                    "verified_faketls_handshakes": len(latencies) + len(concurrent_latencies),
                    "sequential_ms": summarize(latencies), "concurrent16_ms": summarize(concurrent_latencies),
                    "rss_bytes_after_probes": memory}
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if os.geteuid() != 0 or not Path(args.root).resolve().name.startswith("tdh-audit-"):
        raise SystemExit("Use root on a disposable host with a dedicated tdh-audit-* directory")
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    results = [run_version(Path(args.root), version) for version in ("3.5.5", "3.5.14")]
    result = {"scope": "loopback synthetic identities, nobody UID; no MTProto file/DC throughput or production claim",
              "kernel": os.uname().release, "results": results}
    Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)
