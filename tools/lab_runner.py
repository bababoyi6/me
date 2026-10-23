#!/usr/bin/env python3
"""Run approved synthetic labs in a bounded transient systemd cgroup, with host safeguards."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time
import uuid

from lab_preflight import inventory, MIB, read, run

REPO = Path(__file__).resolve().parents[1]


def systemd_command(unit, limits, worker, seconds):
    properties = {"MemoryMax": limits["memory_max_bytes"], "MemoryHigh": limits["memory_high_bytes"],
                  "MemorySwapMax": 0, "CPUQuota": "60%", "TasksMax": limits["tasks_max"],
                  "RuntimeMaxSec": seconds, "TimeoutStopSec": 20, "KillMode": "control-group",
                  "OOMPolicy": "stop", "LimitFSIZE": limits["output_max_bytes"], "UMask": "0077",
                  "WorkingDirectory": str(REPO)}
    return ["systemd-run", "--quiet", "--wait", "--pipe", "--collect", "--service-type=exec", "--unit=" + unit,
            *["--property=" + key + "=" + str(value) for key, value in properties.items()],
            "--setenv=PATH=" + os.environ.get("PATH", "/usr/sbin:/usr/bin:/sbin:/bin"),
            "--setenv=LD_LIBRARY_PATH=" + os.environ.get("LD_LIBRARY_PATH", ""), "--", *worker]


def cgroup_sample(group):
    if not group:
        return {}
    base = Path("/sys/fs/cgroup") / group.lstrip("/")
    result = {}
    for name in ("memory.current", "memory.peak", "memory.swap.current", "pids.current"):
        value = read(base / name).strip()
        result[name] = int(value) if value.isdigit() else None
    for name in ("memory.events", "cpu.stat"):
        result[name] = dict((parts[0], int(parts[1])) for line in read(base / name).splitlines()
                            if len(parts := line.split()) == 2 and parts[1].isdigit())
    return result


def output_bytes(root):
    return sum(path.stat().st_size for folder in (root / "results", *root.glob("run-tdh-audit-*"))
               if folder.is_dir() for path in folder.rglob("*") if path.is_file() and not path.is_symlink())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--haproxy", default="haproxy")
    parser.add_argument("--wg", default="wg")
    parser.add_argument("--profile", choices=("smoke", "ab", "soak"), default="smoke")
    parser.add_argument("--rtt", type=int, choices=(20, 50, 100, 200), default=100)
    parser.add_argument("--max-streams", type=int, choices=(1, 4, 8, 16, 32), default=4)
    parser.add_argument("--payload-mib", type=int, choices=(8, 16, 64, 256), default=16)
    parser.add_argument("--rate-mbps", type=int, choices=(100, 1000), default=100)
    parser.add_argument("--rounds", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--execute", action="store_true", help="without this flag only prints preflight/plan")
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("Linux is required")
    report = inventory()
    root = Path(args.root).absolute()
    if root.is_symlink() or not root.name.startswith("tdh-audit-"):
        parser.error("use a dedicated non-symlink tdh-audit-* directory")
    if args.profile != "ab" and (args.max_streams != 4 or args.payload_mib != 16 or args.rounds != 3 or args.rate_mbps != 100):
        parser.error("load options only apply to the ab profile")
    if args.profile == "ab":
        worker = [sys.executable, str(REPO / "tests/upload_ab.py"), "--root", str(root), "--haproxy", args.haproxy,
                  "--wg", args.wg, "--rtt", str(args.rtt), "--max-streams", str(args.max_streams),
                  "--payload-mib", str(args.payload_mib), "--rounds", str(args.rounds), "--rate-mbps", str(args.rate_mbps)]
    else:
        worker = [sys.executable, str(REPO / "tests/dualhop_lab.py"), "--root", str(root), "--build", "auto",
                  "--haproxy", args.haproxy, "--wg", args.wg, "--rtt", str(args.rtt), "--separate-dc",
                  *(["--soak"] if args.profile == "soak" else []), "run"]
    unit = "tdh-audit-" + uuid.uuid4().hex[:12]
    command = systemd_command(unit, report["budget"], worker, 600)
    plan = {"preflight": report, "command": command, "profile": args.profile,
            "classification": "SINGLE_VPS_VIRTUAL_3NODE" if args.profile != "ab" else "SINGLE_VPS_SYNTHETIC"}
    if not args.execute:
        print(json.dumps(plan, indent=2))
        return 0
    if os.geteuid() != 0 or not report["budget"]["eligible"]:
        parser.error("root and a safe current resource budget are required")
    if not {"memory", "cpu", "pids"}.issubset(report["cgroup_v2_controllers"]):
        parser.error("cgroup v2 memory/cpu/pids controllers are required; no unbounded fallback")
    if report["existing_lab_namespaces"]:
        parser.error("existing lab namespaces require ownership review before a new run")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.stat().st_uid != 0 or root.stat().st_mode & 0o077:
        parser.error("lab root must be private and root-owned")
    # Each run preserves its failures and output in a new directory.
    results = root / ("run-" + unit)
    results.mkdir(mode=0o700)
    revision = run("git", "-C", str(REPO), "rev-parse", "HEAD")
    packaged = json.loads(read(REPO / "SOURCE_STATE.json") or "{}")
    plan["git_commit"] = revision or packaged.get("base_commit")
    plan["git_dirty"] = bool(run("git", "-C", str(REPO), "status", "--porcelain")) if revision else packaged.get("dirty")
    plan["source_sha256"] = {str(path.relative_to(REPO)): hashlib.sha256(path.read_bytes()).hexdigest()
                            for path in [REPO / "telemt-dual-hop.sh", *sorted((REPO / "tests").glob("*.py")),
                                         *sorted((REPO / "tools").glob("*.py"))]}
    (results / "manifest.json").write_text(json.dumps(plan, indent=2) + "\n")
    samples, reason, group = [], None, ""
    initial_output_bytes = output_bytes(root)
    def stop(signum, frame):
        raise KeyboardInterrupt()
    previous = signal.signal(signal.SIGTERM, stop)
    process = None
    try:
        with (results / "worker.log").open("xb") as output:
            process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT)
            started = time.monotonic()
            while process.poll() is None:
                if not group:
                    group = run("systemctl", "show", unit, "--property=ControlGroup", "--value")
                sample = cgroup_sample(group)
                sample["elapsed_seconds"] = time.monotonic() - started
                available = next((int(line.split()[1]) * 1024 for line in read("/proc/meminfo").splitlines()
                                  if line.startswith("MemAvailable:")), 0)
                sample["host_memory_available_bytes"] = available
                sample["new_output_bytes"] = output_bytes(root) - initial_output_bytes
                samples.append(sample)
                oom = next((int(line.split()[1]) for line in read("/proc/vmstat").splitlines()
                            if line.startswith("oom_kill ")), None)
                if available < report["budget"]["host_reserve_bytes"]:
                    reason = "host_memory_reserve"
                elif oom != report["kernel_oom_kill_count"]:
                    reason = "host_oom_count_changed"
                elif sample.get("memory.events", {}).get("oom", 0):
                    reason = "cgroup_oom"
                elif (results / "worker.log").stat().st_size >= report["budget"]["output_max_bytes"]:
                    reason = "output_budget"
                elif sample["new_output_bytes"] >= report["budget"]["output_max_bytes"]:
                    reason = "total_output_budget"
                elif shutil.disk_usage(root).free < 512 * MIB:
                    reason = "host_disk_reserve"
                elif time.monotonic() - started > 610:
                    reason = "supervisor_deadline"
                if reason:
                    break
                time.sleep(.5)
    except KeyboardInterrupt:
        reason = "operator_interrupt"
    finally:
        signal.signal(signal.SIGTERM, previous)
        # This unit name is freshly generated by this supervisor, never a host service.
        run("systemctl", "stop", unit)
        if process:
            try:
                process.wait(timeout=25)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        remaining = [line.split()[0] for line in run("ip", "netns", "list").splitlines()
                     if line.startswith("tdh-audit-")]
        key_files = [path.name for path in root.glob("*.key")]
        final_cgroup = cgroup_sample(group)
        result = {"finished_utc": datetime.now(timezone.utc).isoformat(), "stop_reason": reason,
                  "exit": process.returncode if process else None, "samples": samples,
                  "remaining_lab_namespaces": remaining, "remaining_lab_key_files": key_files,
                  "final_cgroup_sample": final_cgroup,
                  "cleanup_verified": not remaining and not key_files and not final_cgroup.get("pids.current"),
                  "warning": "Existing connections cannot migrate on failover; no real Telegram capacity claim."}
        (results / "supervisor.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"results": str(results), "exit": result["exit"], "stop_reason": reason,
                      "cleanup_verified": result["cleanup_verified"]}))
    return 0 if result["exit"] == 0 and reason is None and result["cleanup_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
