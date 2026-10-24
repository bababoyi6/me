#!/usr/bin/env python3
"""Sequential, paired upload comparison of flush-only and audit2; synthetic DC segment only."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

from network_lab import Network, NAMES, memory_available
from lab_files import file_sha256

HERE = Path(__file__).resolve().parent


def paired_order(round_index, builds=("flush", "auto")):
    return builds if round_index % 2 == 0 else builds[::-1]


def policy_order(round_index,family='all'):
    modes={'all':("pass","auto","client-auto","dc-auto"),
           'client':('pass','client-auto'),'dc':('pass','dc-auto'),
           'lowat':('pass','auto-lowat'),'queue':('pass','auto','auto-lowat')}[family]
    return modes if round_index % 2 == 0 else modes[::-1]


def wait_worker(worker):
    """Always return a recorded failure even if graceful termination stalls."""
    try:
        return worker.wait(timeout=100)
    except subprocess.TimeoutExpired:
        worker.terminate()
        try:
            worker.wait(timeout=10)
        except subprocess.TimeoutExpired:
            worker.kill()
            try:
                worker.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass  # supervisor still owns the entire process group
        return 124


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--haproxy", default="haproxy")
    parser.add_argument("--wg", default="wg")
    parser.add_argument("--rtt", type=int, choices=(20, 50, 100, 200), default=100)
    parser.add_argument("--max-streams", type=int, choices=(1, 4, 8, 16, 32), default=4)
    parser.add_argument("--min-streams", type=int, choices=(1, 4, 8, 16, 32), default=1)
    parser.add_argument("--payload-mib", type=int, choices=(8, 16, 64, 128, 256), default=16)
    parser.add_argument("--rate-mbps", type=int, choices=(100, 1000), default=100)
    parser.add_argument("--rounds", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--socket-details", action="store_true")
    parser.add_argument("--socket-library", help="compare same-binary socket policies, private mock DC only")
    parser.add_argument('--core-pair', choices=('historical', 'fairness'), default='historical')
    parser.add_argument('--mock-receive-buffer',type=int,choices=(0,262144),default=0)
    parser.add_argument('--client-send-buffer',type=int,choices=(0,262144),default=0)
    parser.add_argument('--policy-family',choices=('all','client','dc','lowat','queue'),default='all')
    args = parser.parse_args()
    if args.min_streams > args.max_streams:
        parser.error("minimum stream tier exceeds maximum")
    if args.policy_family!='all' and not args.socket_library:
        parser.error('policy family requires the private lab library')
    if args.core_pair != 'historical' and args.socket_library:
        parser.error('native core comparison cannot preload a socket policy')
    if args.payload_mib > 128 and args.max_streams < 4:
        parser.error("per-stream payload cannot exceed 128 MiB")
    root = Path(args.root).resolve()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    net = Network(SimpleNamespace(root=str(root), wg=args.wg, start_iperf=False, rtt_ms=args.rtt,
                                  loss_percent=0, rate_mbps=args.rate_mbps, mtu=1420))
    rows = []
    run_id = "ab-" + str(time.time_ns())
    destination = root / "results" / (run_id + ".json")
    manifest = {"classification": "SINGLE_VPS_SYNTHETIC", "date_utc": datetime.now(timezone.utc).isoformat(),
                "control": "3.5.5+tdh.audit1 flush-only; not unmodified 1.5.0",
                "candidate": "3.5.5+tdh.audit2; identical to 1.5.0 default core",
                "scope": "raw TCP mock DC segment; WG configured but payload bypasses WG; direct-to-Telemt upload",
                "rtt_ms_configured": args.rtt, "rate_mbps_per_direction": args.rate_mbps,
                "payload_bytes_total_per_trial": args.payload_mib * 1024**2, "direction": "U", "kernel": os.uname().release,
                "command": sys.argv, "rounds": args.rounds, "rows": rows, "complete": False}
    manifest["socket_observation"] = "detailed" if args.socket_details else "off"
    builds = ('control', 'fair') if args.core_pair == 'fairness' else ('flush', 'auto')
    manifest.update(control_build=builds[0], candidate_build=builds[1])
    if args.core_pair == 'fairness':
        manifest.update(control='CI audit2 control with automatic socket buffers',
                        candidate='same-toolchain fair1 with 64KiB ready-poll quantum',
                        core_pair='fairness')
    manifest['mock_dc_receive_buffer_requested']=args.mock_receive_buffer
    manifest['client_send_buffer_requested']=args.client_send_buffer
    if args.socket_library:
        manifest['socket_policy_cases']=list(policy_order(0,args.policy_family))
        manifest['classification']='SINGLE_VPS_SOCKET_POLICY_CAUSAL'
        manifest['control']='same audit1 flush-only binary with pass-through interposer'
        manifest['candidate']='same audit1 binary; only socket buffer policy changed'
        manifest['socket_library_sha256']=hashlib.sha256(Path(args.socket_library).read_bytes()).hexdigest()
    def save():
        destination.write_text(json.dumps(manifest, indent=2) + "\n")
    try:
        destination.parent.mkdir(mode=0o700, exist_ok=True)
        manifest["binary_sha256"] = {build: file_sha256(root / ("telemt-" + build))
                                     for build in builds}
        save()
        net.setup()
        with tempfile.TemporaryDirectory(prefix="tdh-audit-ab-") as folder:
            shared = Path(folder)
            os.chown(shared, 65534, 65534)
            status = shared / "dc.json"
            dc = subprocess.Popen(["ip", "netns", "exec", NAMES[2], sys.executable,
                                   str(HERE / "mock_dc_worker.py"), "--output", str(status), "--socket-timeout", "25",
                                   '--receive-buffer',str(args.mock_receive_buffer)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            net.processes.append(dc)
            try:
                deadline = time.monotonic() + 5
                while not status.exists():
                    if dc.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError("mock DC readiness failed")
                    time.sleep(.1)
                tiers = [streams for streams in (1, 4, 8, 16, 32) if args.min_streams <= streams <= args.max_streams]
                # Fixed total payload per tier and paired order; no concurrent competing A/B workers.
                for streams in tiers:
                    if args.payload_mib * 1024**2 // streams > 128 * 1024**2:
                        continue
                    for turn in range(args.rounds):
                        cases=[('flush',mode) for mode in policy_order(turn,args.policy_family)] if args.socket_library else [(b,'pass') for b in paired_order(turn, builds)]
                        for build,mode in cases:
                            if memory_available() < 256 * 1024**2:
                                raise RuntimeError("host memory margin before next load")
                            variant=mode if args.socket_library else build
                            name = f"{run_id}-{streams}-{turn}-{variant}.json"
                            output = destination.parent / name
                            argv = ["ip", "netns", "exec", NAMES[0], sys.executable, str(HERE / "high_rtt_worker.py"),
                                    "--root", str(root), "--haproxy", args.haproxy, "--build", build,
                                    "--output", str(output), "--rounds", "1", "--streams", str(streams),
                                    "--bytes", str(args.payload_mib * 1024**2), "--direction", "U",
                                    "--slow-readers", "--dc-timeout", "25", "--rate-mbps", str(args.rate_mbps),
                                    "--small-probes", "--observe-network", "--socket-observation",
                                    "detailed" if args.socket_details else "off"]
                            argv += ['--client-send-buffer',str(args.client_send_buffer)]
                            if args.socket_library:
                                argv += ['--socket-library',args.socket_library,'--socket-policy',mode]
                            log_name = name.removesuffix('.json') + '.log'
                            log_file = (destination.parent / log_name).open('xb')
                            worker = subprocess.Popen(argv, stdout=log_file, stderr=subprocess.STDOUT)
                            net.processes.append(worker)
                            try:
                                code = wait_worker(worker)
                            finally:
                                log_file.close()
                            row = {"pair_round": turn + 1, "streams": streams, "build": build,
                                   "socket_policy":mode,
                                   "exit": code, "result_file": name, "worker_log": log_name, "command": argv}
                            if output.exists():
                                row["trial"] = json.loads(output.read_text())
                            rows.append(row)
                            save()
                            if code:
                                raise RuntimeError("paired trial failed; retained; refusing higher load")
                manifest["complete"] = True
            finally:
                if status.exists():
                    manifest["mock_dc"] = json.loads(status.read_text())
                dc.terminate()
                try:
                    dc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    dc.kill()
                    dc.wait(timeout=5)
    except BaseException as exc:
        manifest["failure"] = type(exc).__name__
        raise
    finally:
        try:
            net.close()
        finally:
            save()
    print(json.dumps({"result": str(destination), "complete": manifest["complete"], "paired_trials": len(rows)}))


if __name__ == "__main__":
    main()
