#!/usr/bin/env python3
"""Run approved synthetic labs in a bounded transient systemd cgroup, with host safeguards."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys
import time
import uuid

from lab_preflight import inventory, MIB, read, run, run_checked, escalation_budget

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/'tests'))
from lab_artifacts import cleanup_artifacts
sys.path.pop(0)


def write_private_report(path, value):
    """Create owner-only evidence regardless of the invoking shell's umask."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
        output.write(json.dumps(value, indent=2) + '\n')


def systemd_command(unit, limits, worker, seconds):
    properties = {"MemoryMax": limits["memory_max_bytes"], "MemoryHigh": limits["memory_high_bytes"],
                  "MemorySwapMax": 0, "CPUQuota": "60%", "TasksMax": limits["tasks_max"],
                  "RuntimeMaxSec": seconds, "TimeoutStopSec": 20, "KillMode": "control-group",
                  "OOMPolicy": "stop", "LimitFSIZE": limits["output_max_bytes"], "UMask": "0077",
                  "WorkingDirectory": str(REPO)}
    return ["systemd-run", "--quiet", "--wait", "--pipe", "--service-type=exec", "--unit=" + unit,
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
    for name in ("memory.events", "memory.stat", "cpu.stat"):
        result[name] = dict((parts[0], int(parts[1])) for line in read(base / name).splitlines()
                            if len(parts := line.split()) == 2 and parts[1].isdigit())
    result["memory.pressure"] = read(base / "memory.pressure").strip()
    return result


def output_bytes(root):
    return sum(path.stat().st_size for folder in (root / "results", *root.glob("run-tdh-audit-*"))
               if folder.is_dir() for path in folder.rglob("*") if path.is_file() and not path.is_symlink())


def namespace_cleanup_status():
    try:
        remaining = [line.split()[0] for line in run_checked("ip", "netns", "list").splitlines()
                     if line.startswith("tdh-audit-")]
        return remaining, []
    except RuntimeError:
        return None, ["namespace_inspection_failed"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--haproxy", default="haproxy")
    parser.add_argument("--wg", default="wg")
    parser.add_argument("--profile", choices=("smoke", "ab", "soak", "observer", "socket", "mixed"), default="smoke")
    parser.add_argument('--logical-clients',type=int,choices=(30,60,100),default=30)
    parser.add_argument("--rtt", type=int, choices=(20, 50, 100, 200), default=100)
    parser.add_argument("--max-streams", type=int, choices=(1, 4, 8, 16, 32), default=4)
    parser.add_argument("--min-streams", type=int, choices=(1, 4, 8, 16, 32), default=1)
    parser.add_argument("--payload-mib", type=int, choices=(8, 16, 64, 128, 256), default=16)
    parser.add_argument("--rate-mbps", type=int, choices=(100, 1000), default=100)
    parser.add_argument("--rounds", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--socket-details", action="store_true")
    parser.add_argument('--mock-receive-buffer',type=int,choices=(0,262144),default=0)
    parser.add_argument('--client-send-buffer',type=int,choices=(0,262144),default=0)
    parser.add_argument('--policy-family',choices=('all','client','dc','lowat','queue'),default='all')
    parser.add_argument('--core-pair',choices=('historical','fairness','queue','queue-fixed','balanced','balanced-fixed','bulk','bulk-fixed'),default='historical')
    parser.add_argument('--candidate-only',action='store_true')
    parser.add_argument('--share-core-file',action='store_true',help='ab only: share the verified private core inode')
    parser.add_argument('--previous-run', help='owned run-tdh-audit-* directory name; require prior measured memory headroom')
    parser.add_argument('--memory-high-percent', type=int, choices=(75, 80), default=75,
                        help='ab only: explicit soft-throttling policy; hard memory/host reserve remain unchanged')
    parser.add_argument("--execute", action="store_true", help="without this flag only prints preflight/plan")
    args = parser.parse_args()
    if args.min_streams > args.max_streams:
        parser.error("minimum stream tier exceeds maximum")
    if sys.platform != "linux":
        parser.error("Linux is required")
    if args.memory_high_percent != 75 and args.profile != 'ab':
        parser.error('non-default soft memory policy requires the private ab profile')
    report = inventory(memory_high_percent=args.memory_high_percent)
    before_temp_names={p.name for p in Path('/tmp').glob('tdh-e2e-*') if p.is_dir()}
    root = Path(args.root).absolute()
    if root.is_symlink() or not root.name.startswith("tdh-audit-"):
        parser.error("use a dedicated non-symlink tdh-audit-* directory")
    if args.profile not in ("ab","socket") and (args.max_streams != 4 or args.min_streams != 1 or args.payload_mib != 16 or args.rounds != 3 or args.rate_mbps != 100 or args.socket_details or args.mock_receive_buffer or args.client_send_buffer):
        parser.error("load options only apply to ab/socket profiles")
    if args.profile!='socket' and args.policy_family!='all':
        parser.error('policy families only apply to the socket profile')
    if args.core_pair!='historical' and args.profile!='ab':
        parser.error('native core comparison requires the ab profile')
    if args.share_core_file and args.profile!='ab':
        parser.error('shared core file is confined to the private ab profile')
    if args.candidate_only and (args.profile!='ab' or args.core_pair=='historical' or args.rounds!=1 or args.min_streams!=args.max_streams):
        parser.error('candidate-only requires ab, one round, one tier and a named candidate')
    admission = None
    if args.previous_run:
        if args.profile != 'ab' or not re.fullmatch(r'run-tdh-audit-[0-9a-f]{12}', args.previous_run):
            parser.error('previous-run requires an owned ab run directory name')
        prior = root / args.previous_run
        status = prior / 'supervisor.json'
        try:
            if not root.is_dir() or not prior.is_dir() or not status.is_file():
                raise ValueError('prior run requires directories and a regular supervisor file')
            for path in (root, prior, status):
                metadata = path.stat()
                if path.is_symlink() or metadata.st_uid != 0 or metadata.st_mode & 0o077:
                    raise ValueError('prior run must be private and root-owned')
            with status.open('rb') as stream:
                content = stream.read(32 * MIB + 1)
            if len(content) > 32 * MIB:
                raise ValueError('prior status exceeds bounded size')
            previous = json.loads(content)
            if not isinstance(previous, dict):
                raise ValueError('invalid prior supervisor')
            admission = escalation_budget(previous, report['budget'])
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        if args.execute and not admission['eligible']:
            parser.error(admission['reason'])
    if args.profile!='mixed' and args.logical_clients!=30:
        parser.error('logical client counts only apply to mixed profile')
    if args.profile in ("ab","socket"):
        worker = [sys.executable, str(REPO / "tests/upload_ab.py"), "--root", str(root), "--haproxy", args.haproxy,
                  "--wg", args.wg, "--rtt", str(args.rtt), "--max-streams", str(args.max_streams),
                  "--min-streams", str(args.min_streams),
                  "--payload-mib", str(args.payload_mib), "--rounds", str(args.rounds), "--rate-mbps", str(args.rate_mbps)]
        worker += ['--mock-receive-buffer',str(args.mock_receive_buffer)]
        worker += ['--client-send-buffer',str(args.client_send_buffer)]
        worker += ['--core-pair',args.core_pair]
        if args.candidate_only:
            worker.append('--candidate-only')
        if args.share_core_file:
            worker.append('--share-core-file')
        if args.socket_details:
            worker.append("--socket-details")
        if args.profile == "socket":
            worker += ["--socket-library",str(root/"socket-policy.so")]
            worker += ['--policy-family',args.policy_family]
    elif args.profile == 'mixed':
        worker=[sys.executable,str(REPO/'tests/mixed_clients.py'),'--root',str(root),
                '--haproxy',args.haproxy,'--clients',str(args.logical_clients)]
    elif args.profile == "observer":
        worker = [sys.executable, str(REPO / "tests/observer_oracle.py"), "--root", str(root), "--wg", args.wg]
    else:
        worker = [sys.executable, str(REPO / "tests/dualhop_lab.py"), "--root", str(root), "--build", "auto",
                  "--haproxy", args.haproxy, "--wg", args.wg, "--rtt", str(args.rtt), "--separate-dc",
                  *(["--soak"] if args.profile == "soak" else []), "run"]
    unit = "tdh-audit-" + uuid.uuid4().hex[:12]
    command = systemd_command(unit, report["budget"], worker, 600)
    if args.profile=='mixed':
        command.insert(command.index('--'),'--property=PrivateNetwork=yes')
    plan = {"preflight": report, "command": command, "profile": args.profile,
            'temporary_directories_before':sorted(before_temp_names),
            "classification": "SINGLE_VPS_VIRTUAL_3NODE" if args.profile != "ab" else "SINGLE_VPS_SYNTHETIC"}
    if admission is not None:
        plan['escalation_admission'] = admission
        plan['previous_run'] = args.previous_run
    if args.profile == "observer":
        plan["classification"] = "SINGLE_VPS_OBSERVER_ORACLE"
    if args.profile == "socket":
        plan["classification"] = "SINGLE_VPS_SOCKET_POLICY_CAUSAL"
    if args.profile=='mixed':
        plan['classification']='SINGLE_VPS_SYNTHETIC_LOOPBACK_MIXED'
    if args.candidate_only:
        plan['classification']='SINGLE_VPS_CANDIDATE_SMOKE'
    if not args.execute:
        print(json.dumps(plan, indent=2))
        return 0
    if os.geteuid() != 0 or not report["budget"]["eligible"]:
        parser.error("root and a safe current resource budget are required")
    if not {"memory", "cpu", "pids"}.issubset(report["cgroup_v2_controllers"]):
        parser.error("cgroup v2 memory/cpu/pids controllers are required; no unbounded fallback")
    if report["existing_lab_namespaces"]:
        parser.error("existing lab namespaces require ownership review before a new run")
    if report["kernel_oom_kill_count"] is None:
        parser.error("host OOM counter is unavailable; no load permitted")
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
    write_private_report(results / "manifest.json", plan)
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
        unit_result = run("systemctl", "show", unit, "--property=Result", "--value")
        unit_status = run("systemctl", "show", unit, "--property=ExecMainStatus", "--value")
        run("systemctl", "stop", unit)
        if process:
            try:
                process.wait(timeout=25)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        remaining, inspection_errors = namespace_cleanup_status()
        key_files = [path.name for path in root.glob("*.key")]
        final_cgroup = cgroup_sample(group)
        unit_state=run('systemctl','show',unit,'--property=ActiveState','--value')
        known_group_absent=bool(group) and not (Path('/sys/fs/cgroup')/group.lstrip('/')).exists()
        stopped=(unit_state in ('inactive','failed') or known_group_absent) and (final_cgroup.get('pids.current')==0 or known_group_absent)
        artifact_cleanup=cleanup_artifacts(root/'artifact-registry',stopped)
        unattributed_temporaries=sorted(p.name for p in Path('/tmp').glob('tdh-e2e-*')
                                       if p.is_dir() and p.name not in before_temp_names)
        final_oom = next((int(line.split()[1]) for line in read("/proc/vmstat").splitlines()
                          if line.startswith("oom_kill ")), None)
        oom_delta = final_oom - report["kernel_oom_kill_count"] if final_oom is not None and report["kernel_oom_kill_count"] is not None else None
        if oom_delta and reason is None:
            reason = "host_oom_count_changed"
        result = {"finished_utc": datetime.now(timezone.utc).isoformat(), "stop_reason": reason,
                  "unit_result": unit_result or None, "unit_exec_main_status": unit_status or None,
                  "host_oom_kill_delta": oom_delta,
                  "exit": process.returncode if process else None, "samples": samples,
                  "remaining_lab_namespaces": remaining, "remaining_lab_key_files": key_files,
                  "final_cgroup_sample": final_cgroup,
                  'temporary_artifact_cleanup':artifact_cleanup,
                  'unattributed_temporary_directories':unattributed_temporaries,
                  'cleanup_inspection_errors':inspection_errors,
                  "cleanup_verified": not inspection_errors and not remaining and not key_files and stopped and not artifact_cleanup['errors'] and not unattributed_temporaries,
                  "warning": "Existing connections cannot migrate on failover; no real Telegram capacity claim."}
        write_private_report(results / "supervisor.json", result)
        # Retain failed unit diagnostics until captured, then release only our unit.
        run("systemctl", "reset-failed", unit)
    print(json.dumps({"results": str(results), "exit": result["exit"], "stop_reason": reason,
                      "cleanup_verified": result["cleanup_verified"]}))
    return 0 if result["exit"] == 0 and reason is None and result["cleanup_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
