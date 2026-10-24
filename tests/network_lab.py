#!/usr/bin/env python3
"""Bounded, root-only TCP/WireGuard experiment inside private network namespaces.

Only run on a disposable test server. Host routes, firewall and NIC are untouched.
Netem runs on an intermediate namespace, away from the TCP sender's qdisc.
"""
import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import statistics
import stat
import subprocess
import time

NAMES = ("tdh-audit-a", "tdh-audit-wan", "tdh-audit-b")
RESERVED_NAMES = (*NAMES, "tdh-audit-b2", "tdh-audit-dc")
GLOBAL_LOCK = Path("/run/lock/tdh-audit-network.lock")
ROOT_UID = 0


def exclusive_lock(path):
    """Do not follow a pre-existing symlink or open/truncate an unrelated file."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != ROOT_UID or info.st_nlink != 1 or info.st_mode & 0o077:
            raise RuntimeError("lab lock must be a private root-owned regular file")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return os.fdopen(fd, "r+")
    except BaseException:
        os.close(fd)
        raise


def command(*args, timeout=10, input=None):
    try:
        return subprocess.run(args, input=input, check=True, text=True, capture_output=True, timeout=timeout).stdout.strip()
    except subprocess.CalledProcessError as exc:
        # Operation names only: never include key arguments or raw command output.
        operation = list(args[:5]) if args[0] == "ip" else [Path(args[0]).name]
        raise RuntimeError(json.dumps({"operation": operation, "exit": exc.returncode})) from None


def summary(values):
    values = sorted(values)
    return {"median": statistics.median(values), "p95": values[math.ceil(.95 * len(values)) - 1],
            "min": min(values), "max": max(values)}


def memory_available():
    return next(int(x.split()[1]) * 1024 for x in Path("/proc/meminfo").read_text().splitlines() if x.startswith("MemAvailable:"))


def cpu_snapshot():
    return list(map(int, Path('/proc/stat').read_text().splitlines()[0].split()[1:9]))


def cpu_interval(before, after):
    delta = [new-old for old,new in zip(before,after)]
    if len(delta) != 8 or any(value < 0 for value in delta) or not sum(delta):
        return None
    total = sum(delta)
    # guest time is already included in user/nice; steal is unavailable vCPU
    # time and must not be misreported as the proxy consuming CPU.
    return {'busy_percent':100*sum(delta[index] for index in (0,1,2,5,6))/total,
            'steal_percent':100*delta[7]/total,'iowait_percent':100*delta[4]/total}


class Network:
    def __init__(self, args):
        self.args = args
        self.processes = []
        self.created = []
        self.key_paths = []
        supplied = Path(args.root)
        self.root = supplied.resolve()
        if os.geteuid() != 0 or not self.root.name.startswith("tdh-audit-"):
            raise RuntimeError("requires root and a dedicated tdh-audit-* directory")
        if supplied.is_symlink():
            raise RuntimeError("lab directory must not be a symlink")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.stat().st_mode & 0o077 or self.root.stat().st_uid != ROOT_UID:
            raise RuntimeError("lab directory must be root-owned and private (0700)")
        self.global_lock = exclusive_lock(GLOBAL_LOCK)
        try:
            self.lock = exclusive_lock(self.root / "network-lab.lock")
            try:
                existing = {line.split()[0] for line in command("ip", "netns", "list").splitlines()}
                if existing.intersection(RESERVED_NAMES):
                    raise RuntimeError("refusing to reuse existing namespaces")
            except BaseException:
                self.lock.close()
                raise
        except BaseException:
            self.global_lock.close()
            raise

    def setup(self):
        for name in NAMES:
            command("ip", "netns", "add", name)
            self.created.append(name)
            command("ip", "-n", name, "link", "set", "lo", "up")
        for left, right, a, b, prefix in (
            (NAMES[0], NAMES[1], "192.0.2.1", "192.0.2.2", "la"),
            (NAMES[1], NAMES[2], "198.51.100.1", "198.51.100.2", "lb"),
        ):
            first, second = "tdh-" + prefix + "0", "tdh-" + prefix + "1"
            # Create both ends inside their namespaces; no transient host links.
            command("ip", "-n", left, "link", "add", first, "type", "veth", "peer", "name", second, "netns", right)
            for ns, dev, address in ((left, first, a), (right, second, b)):
                command("ip", "-n", ns, "address", "add", address + "/30", "dev", dev)
                command("ip", "-n", ns, "link", "set", dev, "up")
                # Keep loss at packet granularity instead of dropping a GSO batch.
                command("ip", "netns", "exec", ns, "ethtool", "-K", dev, "gro", "off", "gso", "off", "tso", "off")
        command("ip", "-n", NAMES[0], "route", "add", "default", "via", "192.0.2.2")
        command("ip", "-n", NAMES[2], "route", "add", "default", "via", "198.51.100.1")
        command("ip", "netns", "exec", NAMES[1], "sysctl", "-qw", "net.ipv4.ip_forward=1")
        for ns, dev in ((NAMES[0], "tdh-la0"), (NAMES[2], "tdh-lb1")):
            command("ip", "netns", "exec", ns, "sysctl", "-qw", "net.ipv4.tcp_congestion_control=bbr")
            command("ip", "netns", "exec", ns, "tc", "qdisc", "replace", "dev", dev, "root", "fq")
        for ns, dev in ((NAMES[1], "tdh-la1"), (NAMES[1], "tdh-lb0")):
            args = ["ip", "netns", "exec", ns, "tc", "qdisc", "replace", "dev", dev, "root", "netem",
                    "limit", "10000", "delay", f"{self.args.rtt_ms / 2:g}ms"]
            if self.args.loss_percent:
                args += ["loss", "random", f"{self.args.loss_percent:g}%"]
            args += ["rate", f"{self.args.rate_mbps}mbit"]
            command(*args)
        keys = []
        for index in range(2):
            path = self.root / f"wg-{index}.key"
            key = command(self.args.wg, "genkey") + "\n"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            self.key_paths.append(path)
            with os.fdopen(fd, "w") as stream:
                stream.write(key)
            public = command(self.args.wg, "pubkey", input=key)
            keys.append((path, public))
        for index, ns in enumerate((NAMES[0], NAMES[2])):
            command("ip", "-n", ns, "link", "add", "wg-lab", "type", "wireguard")
            local, peer = ("10.77.1.1", "10.77.1.2") if index == 0 else ("10.77.1.2", "10.77.1.1")
            endpoint = "198.51.100.2" if index == 0 else "192.0.2.1"
            command("ip", "-n", ns, "address", "add", local + "/30", "dev", "wg-lab")
            peer_args = ["endpoint", endpoint + ":51821", "persistent-keepalive", "25"] if index == 0 else []
            command("ip", "netns", "exec", ns, self.args.wg, "set", "wg-lab", "listen-port", "51821",
                    "private-key", str(keys[index][0]), "peer", keys[1-index][1],
                    "allowed-ips", peer + "/32", *peer_args)
            command("ip", "-n", ns, "link", "set", "wg-lab", "mtu", str(self.args.mtu), "up")
        command("ip", "netns", "exec", NAMES[0], "ping", "-n", "-c", "4", "-W", "3", "10.77.1.2", timeout=15)
        if getattr(self.args, "start_iperf", True):
            for bind in ("198.51.100.2", "10.77.1.2"):
                process = subprocess.Popen(["ip", "netns", "exec", NAMES[2], self.args.iperf,
                                            "-s", "-B", bind, "-p", "5201"],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.processes.append(process)
        time.sleep(.4)
        if any(p.poll() is not None for p in self.processes):
            raise RuntimeError("private iperf server failed")

    def sample(self):
        stat = list(map(int, Path("/proc/stat").read_text().splitlines()[0].split()[1:9]))
        return stat, memory_available()

    def run(self):
        rows = []
        progress = self.root / "results" / (self.args.label + "-progress.json")
        progress.parent.mkdir(exist_ok=True)
        for turn in range(self.args.rounds):
            for streams in (1, 4):
                for reverse in (False, True):
                    order = ("raw", "wireguard") if turn % 2 == 0 else ("wireguard", "raw")
                    for mode in order:
                        if memory_available() < 192 * 1024 * 1024:
                            raise RuntimeError("memory safety margin reached")
                        address = "198.51.100.2" if mode == "raw" else "10.77.1.2"
                        cmd = ["ip", "netns", "exec", NAMES[0], self.args.iperf, "-4", "-c", address,
                               "-t", str(self.args.seconds), "-O", "2", "-P", str(streams),
                               "-b", str(self.args.rate_mbps * 1000000 // streams), "-J", "--connect-timeout", "3000"]
                        if reverse:
                            cmd += ["-R"]
                        before, memory = self.sample()
                        started = time.monotonic()
                        text = command(*cmd, timeout=self.args.seconds + 15)
                        elapsed = time.monotonic() - started
                        payload = json.loads(text)
                        if payload.get("error"):
                            raise RuntimeError("iperf failed; raw endpoint details withheld")
                        end = payload["end"]
                        after, memory_end = self.sample()
                        delta = [b - a for a, b in zip(before, after)]
                        cpu_seconds = (sum(delta) - delta[3] - delta[4] - delta[7]) / os.sysconf("SC_CLK_TCK")
                        received = end["sum_received"]
                        row = {"round": turn + 1, "streams": streams, "reverse": reverse, "mode": mode,
                               "receiver_mbps": received["bits_per_second"] / 1e6,
                               "retransmits": end["sum_sent"].get("retransmits"),
                               "whole_vm_busy_cpu_seconds": cpu_seconds,
                               "whole_vm_steal_seconds": delta[7] / os.sysconf("SC_CLK_TCK"),
                               "elapsed_seconds": elapsed,
                               "minimum_available_memory_bytes": min(memory, memory_end)}
                        rows.append(row)
                        progress.write_text(json.dumps({"completed": len(rows), "last": row}, indent=2) + "\n")
                        time.sleep(.5)
        grouped = []
        for streams in (1, 4):
            for reverse in (False, True):
                for mode in ("raw", "wireguard"):
                    selected = [r for r in rows if (r["streams"], r["reverse"], r["mode"]) == (streams, reverse, mode)]
                    grouped.append({"streams": streams, "reverse": reverse, "mode": mode,
                                    "receiver_mbps": summary([r["receiver_mbps"] for r in selected]),
                                    "whole_vm_busy_cpu_seconds": summary([r["whole_vm_busy_cpu_seconds"] for r in selected])})
        result = {"scope": "single VPS private namespaces; synthetic path, not production or Telegram throughput",
                  "kernel": os.uname().release, "rtt_ms_configured": self.args.rtt_ms,
                  "loss_percent_per_direction": self.args.loss_percent, "rate_mbps_per_direction": self.args.rate_mbps,
                  "wireguard_mtu": self.args.mtu, "seconds": self.args.seconds, "warmup_seconds": 2,
                  "rounds": self.args.rounds, "congestion_control": "bbr", "veth_offload": "disabled for packet loss realism",
                  "netem_placement": "intermediate router egress, both directions", "rows": rows, "summary": grouped}
        destination = self.root / "results" / (self.args.label + ".json")
        destination.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps({"result": destination.name, "completed": len(rows), "summary": grouped}), flush=True)

    def close(self):
        failures = []
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
        for process in self.processes:
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
        for name in reversed(self.created):
            try:
                result = subprocess.run(["ip", "netns", "del", name], stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, timeout=3)
                if result.returncode != 0:
                    failures.append(name)
            except (OSError, subprocess.SubprocessError):
                failures.append(name)
        for path in self.key_paths:
            path.unlink(missing_ok=True)
        self.lock.close()
        self.global_lock.close()
        if failures:
            raise RuntimeError("namespace cleanup incomplete: " + ", ".join(failures))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--iperf", default="iperf3")
    parser.add_argument("--wg", default="wg")
    parser.add_argument("--label", choices=("clean", "rtt100", "lossy200", "mtu1280"), required=True)
    parser.add_argument("--rtt-ms", type=int, choices=range(0, 301), default=0)
    parser.add_argument("--loss-percent", type=float, choices=(0, .1, .5, 1), default=0)
    parser.add_argument("--rate-mbps", type=int, choices=range(10, 201), default=100)
    parser.add_argument("--mtu", type=int, choices=(1280, 1360, 1420), default=1420)
    parser.add_argument("--seconds", type=int, choices=range(3, 13), default=6)
    parser.add_argument("--rounds", type=int, choices=range(3, 8), default=3)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    lab = Network(args)
    try:
        lab.setup()
        lab.run()
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__, "operation": str(exc) if isinstance(exc, RuntimeError) else "withheld"}), flush=True)
        raise SystemExit(1)
    finally:
        lab.close()
