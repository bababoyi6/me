#!/usr/bin/env python3
"""Read-only Linux laboratory inventory; never reads credentials or changes host state."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess

MIB = 1024 ** 2


def read(path):
    try:
        return Path(path).read_text()
    except OSError:
        return ""


def run(*argv):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=5)
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def run_checked(*argv):
    """An empty successful query differs from a failed safety inspection."""
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=5, check=True)
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError('safety inspection failed: ' + Path(argv[0]).name) from None


def budget(total, available, disk_free, memory_high_percent=75):
    """Reserve host headroom; reject rather than expand swap or overcommit memory."""
    if type(memory_high_percent) is not int or memory_high_percent not in (75, 80):
        raise ValueError('memory.high percentage must be the explicit 75 or 80 policy')
    reserve = max(256 * MIB, total // 3)
    memory = min(384 * MIB, (available - reserve) * 3 // 4)
    allowed = total >= 768 * MIB and memory >= 192 * MIB and disk_free >= 1024 * MIB
    return {"eligible": allowed, "host_reserve_bytes": reserve,
            "memory_max_bytes": max(0, memory), "memory_high_bytes": max(0, memory * memory_high_percent // 100),
            "memory_high_percent": memory_high_percent,
            "cpu_quota_percent": 60, "tasks_max": 192, "runtime_max_seconds": 600,
            "output_max_bytes": 32 * MIB, "swap_max_bytes": 0}


def inventory(memory_high_percent=75):
    memory = {line.split(":")[0]: int(line.split()[1]) * 1024
              for line in read("/proc/meminfo").splitlines()
              if line.split(":")[0] in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree")}
    release = dict(line.split("=", 1) for line in read("/etc/os-release").splitlines() if "=" in line)
    listeners = []
    for line in run_checked("ss", "-Hlntu").splitlines():
        fields = line.split()
        if len(fields) >= 5:
            listeners.append({"protocol": fields[0], "port": fields[4].rsplit(":", 1)[-1]})
    namespaces = [line.split()[0] for line in run_checked("ip", "netns", "list").splitlines()]
    disk = shutil.disk_usage("/tmp")
    controllers = read("/sys/fs/cgroup/cgroup.controllers").split()
    resources = budget(memory.get("MemTotal", 0), memory.get("MemAvailable", 0), disk.free, memory_high_percent)
    return {"scope": "READ_ONLY_PREFLIGHT", "date_utc": datetime.now(timezone.utc).isoformat(),
            "os": release.get("PRETTY_NAME", "").strip('"'), "kernel": os.uname().release,
            "architecture": os.uname().machine, "cpus": os.cpu_count(), "euid": os.geteuid(),
            "memory_bytes": memory, "load_average": list(os.getloadavg()),
            "tmp_disk_free_bytes": disk.free, "listeners": listeners,
            "running_service_names": run("systemctl", "list-units", "--type=service", "--state=running",
                                         "--no-legend", "--no-pager", "--plain").splitlines(),
            "virtualization": run("systemd-detect-virt"),
            "cgroup_v2_controllers": controllers,
            "namespace_types": sorted(p.name for p in Path("/proc/self/ns").iterdir()),
            "existing_lab_namespaces": [name for name in namespaces if name.startswith("tdh-audit-")],
            "tools": {name: bool(shutil.which(name)) for name in
                      ("ip", "tc", "wg", "ethtool", "haproxy", "systemd-run", "timeout", "python3")},
            "kernel_oom_kill_count": next((int(line.split()[1]) for line in read("/proc/vmstat").splitlines()
                                           if line.startswith("oom_kill ")), None),
            "budget": resources,
            "permission_note": "Capability and tool presence do not prove namespace/cgroup creation succeeds.",
            "other_workloads": "Require operator review before any load; inventory does not grant permission."}


def escalation_budget(previous, limits):
    """Require headroom above an observed prior tier; never enlarge any limit.

    This is a conservative admission floor, not a higher-tier capacity estimate.
    Runtime memory/OOM safeguards and explicit load authorization still apply.
    """
    successful = (type(previous.get('exit')) is int and previous['exit'] == 0 and
                  previous.get('stop_reason') is None and previous.get('cleanup_verified') is True and
                  type(previous.get('host_oom_kill_delta')) is int and previous['host_oom_kill_delta'] == 0)
    samples = previous.get('samples')
    peaks = [p.get('memory.current') for p in samples if isinstance(p, dict)] if isinstance(samples, list) else []
    peaks = [value for value in peaks if type(value) is int and value > 0]
    peak = max(peaks, default=0)
    required = (peak * 110 + 99) // 100  # 10% margin above the measured prior-tier peak
    high = limits.get('memory_high_bytes')
    admitted = (successful and peak > 0 and limits.get('eligible') is True and
                type(high) is int and high >= required)
    return {'eligible': admitted, 'previous_peak_bytes': peak,
            'required_memory_high_bytes': required, 'current_memory_high_bytes': high,
            'reason': None if admitted else 'prior tier unsuccessful, unmeasured, or insufficient memory.high headroom'}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    if os.name != "posix" or not Path("/proc/meminfo").exists():
        parser.error("Linux /proc is required; no inventory performed")
    print(json.dumps(inventory(), indent=2))
