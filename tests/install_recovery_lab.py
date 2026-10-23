"""Exercise real rollback paths in private mount/network namespaces on a test VPS.

No package installation, host service calls, public listeners or real credentials.
Service/firewall/tuning/account operations are boundaries, recorded as counters.
Only use a disposable root Linux environment and a private tdh-audit-* directory.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

SCRIPT = Path(__file__).resolve().parents[1] / "telemt-dual-hop.sh"
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root", required=True)
parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
parser.add_argument("--case", help=argparse.SUPPRESS)
parser.add_argument("--role", choices=("entry", "backend1", "backend2"), help=argparse.SUPPRESS)
parser.add_argument("--mode", choices=("exit", "error", "term", "int", "hup", "cleanup_hup", "pending", "committed", "legacy"),
                    help=argparse.SUPPRESS)


def private_root(value):
    root = Path(value)
    if os.geteuid() != 0 or not root.is_absolute() or root.is_symlink() or root.resolve() != root:
        raise ValueError("requires root and an absolute private lab directory")
    info = root.stat()
    if not root.name.startswith("tdh-audit-") or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError("requires a root-owned 0700 tdh-audit-* directory")
    return root


def inside(args, root):
    for kind in ("mnt", "net"):
        assert os.readlink(f"/proc/self/ns/{kind}") != os.environ[f"TDH_RECOVERY_HOST_{kind.upper()}"]
    case = Path(args.case)
    assert case.parent == root and case.name.startswith("recovery-case-") and case.resolve() == case
    subprocess.run(["mount", "--make-rprivate", "/"], check=True, timeout=5)
    for folder in ("etc", "usr/local", "var/lib", "run"):
        source = case / folder
        source.mkdir(parents=True, mode=0o700)
        subprocess.run(["mount", "--bind", str(source), "/" + folder], check=True, timeout=5)
    # No host systemd/DBus socket remains reachable through /run.
    assert not Path("/run/systemd/private").exists()
    files = (
        "/etc/telemt-dual-hop/state.env", "/etc/telemt-dual-hop/keys/backend.key",
        "/etc/systemd/system/telemt-dual-hop-telemt.service",
        "/etc/systemd/system/telemt-dual-hop-health-agent.service",
        "/etc/systemd/system/telemt-dual-hop-haproxy.service",
        "/etc/wireguard/tdh-b1.conf", "/etc/wireguard/tdh-b2.conf",
        "/etc/sysctl.d/90-telemt-dual-hop.conf", "/etc/modules-load.d/90-telemt-dual-hop.conf",
        "/usr/local/lib/telemt-dual-hop/telemt", "/var/lib/telemt-dual-hop/work",
        "/usr/local/sbin/telemt-dual-hop", "/usr/local/bin/a",
    )
    for name in files:
        path = Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Managed by Telemt Dual-Hop\nSYNTHETIC_INSTALL_TEST\n")
    unrelated = Path("/etc/unrelated-service.conf")
    unrelated.write_bytes(b"SYNTHETIC_UNRELATED_MUST_REMAIN\n")
    payload = '''set -Eeuo pipefail
TDH_SOURCE_ONLY=1 source "$TDH_RECOVERY_SCRIPT"
systemctl() { printf 'service\\n' >>"$TDH_RECOVERY_CASE/boundaries"; [[ $TDH_RECOVERY_MODE != cleanup_hup ]] || kill -HUP "$BASHPID"; }
remove_ufw_rules() { printf 'firewall\\n' >>"$TDH_RECOVERY_CASE/boundaries"; }
restore_tuning() { printf 'tuning\\n' >>"$TDH_RECOVERY_CASE/boundaries"; }
remove_telemt_account() { printf 'account\\n' >>"$TDH_RECOVERY_CASE/boundaries"; }
TX_ACTIVE=1 TX_OWNER_PID=$BASHPID TX_KIND="$TDH_RECOVERY_ROLE" TDH_ROLE="$TDH_RECOVERY_ROLE"
case $TDH_RECOVERY_MODE in
  exit) exit 7 ;;
  error) false ;;
  term) kill -TERM "$BASHPID" ;;
  int) kill -INT "$BASHPID" ;;
  hup) kill -HUP "$BASHPID" ;;
  cleanup_hup) die "synthetic install failure" ;;
  pending) exit 0 ;;
  committed) TX_ACTIVE=0; exit 0 ;;
  legacy) trap - EXIT; exit 7 ;;
esac
'''
    environment = dict(os.environ, TDH_RECOVERY_SCRIPT=str(SCRIPT), TDH_RECOVERY_CASE=str(case),
                       TDH_RECOVERY_ROLE=args.role, TDH_RECOVERY_MODE=args.mode)
    result = subprocess.run(["bash", "-c", payload], env=environment, capture_output=True, timeout=5)
    expected = {"exit": 7, "error": 1, "term": 143, "int": 130, "hup": 129, "cleanup_hup": 1, "pending": 1, "committed": 0, "legacy": 7}[args.mode]
    assert result.returncode == expected, (args.role, args.mode, result.returncode)
    should_remove = args.mode not in ("committed", "legacy")
    assert Path("/etc/telemt-dual-hop").exists() != should_remove
    assert Path("/usr/local/sbin/telemt-dual-hop").exists() != should_remove
    assert Path("/usr/local/bin/a").exists() != should_remove
    if args.role != "entry":
        interface = "tdh-b1" if args.role == "backend1" else "tdh-b2"
        assert Path(f"/etc/wireguard/{interface}.conf").exists() != should_remove
        assert Path("/usr/local/lib/telemt-dual-hop").exists() != should_remove
        assert Path("/var/lib/telemt-dual-hop").exists() != should_remove
    assert unrelated.read_bytes() == b"SYNTHETIC_UNRELATED_MUST_REMAIN\n"
    print(json.dumps({"role": args.role, "mode": args.mode, "exit": result.returncode,
                      "recovered": should_remove, "unrelated_file_preserved": True,
                      "host_service_firewall_tuning_account_operations": False}))


def run(args, root):
    environment = dict(os.environ, **{f"TDH_RECOVERY_HOST_{kind.upper()}": os.readlink(f"/proc/self/ns/{kind}")
                                     for kind in ("mnt", "net")})
    rows = []
    identities = {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in ("/etc/passwd", "/etc/group")}
    for role in ("entry", "backend1", "backend2"):
        for mode in ("exit", "error", "term", "int", "hup", "cleanup_hup", "pending", "committed", "legacy"):
            case = Path(tempfile.mkdtemp(prefix="recovery-case-", dir=root))
            try:
                command = ["unshare", "--mount", "--net", "--", "python3", str(Path(__file__).resolve()),
                           "--root", str(root), "--inside", "--case", str(case), "--role", role, "--mode", mode]
                result = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=15)
                if result.returncode:
                    raise RuntimeError(f"isolated rollback failed: role={role} mode={mode}; raw output withheld")
                rows.append(json.loads(result.stdout))
            finally:
                assert case.parent == root and case.resolve() == case and case.name.startswith("recovery-case-")
                shutil.rmtree(case)
    assert all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == value for path, value in identities.items())
    report = {"scope": "real file deletion paths in isolated mount and network namespaces; host operations stubbed",
              "manager_sha256": hashlib.sha256(SCRIPT.read_bytes()).hexdigest(), "rows": rows,
              "host_account_files_unchanged": True, "fresh_three_vm_install": False}
    (root / "results").mkdir(mode=0o700, exist_ok=True)
    (root / "results/install-recovery.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"cases": len(rows), "passed": True, "host_account_files_unchanged": True}))


if __name__ == "__main__":
    options = parser.parse_args()
    lab_root = private_root(options.root)
    inside(options, lab_root) if options.inside else run(options, lab_root)
