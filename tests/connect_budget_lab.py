"""Deterministic upstream SYN-delay counterexample in an owned offline namespace."""
import argparse
import hashlib
import os
from pathlib import Path
import signal
import subprocess
import tempfile

from e2e_lab import FullLab, sanitize_startup_log
from stable_acceptance import PINS
from paired_performance import guard, command, resources, write
from core_install import archive_payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stable-archive', required=True)
    parser.add_argument('--balanced2-archive', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    os.umask(0o077)
    backend_cpu, controller_cpu = guard()
    os.sched_setaffinity(0, {controller_cpu})
    report = {'source_commit': os.environ['TDH_SOURCE_COMMIT'], 'scope': 'synthetic DC only; delay outgoing DC SYN by 3500ms, all other packets unchanged',
              'rows': [], 'resources_before': resources(), 'accepted': False}
    try:
        with tempfile.TemporaryDirectory(prefix='tdh-connect-budget-') as directory:
            for kind, pins in PINS.items():
                content, _ = archive_payload(getattr(args, kind+'_archive'), pins[0], pins[2].removeprefix('telemt '), pins[1], False)
                binary = Path(directory)/kind
                binary.write_bytes(content); binary.chmod(0o755)
                for policy in ('upstream-default', 'bounded-retry'):
                    lab = FullLab(binary, offline_lab=True, connect_policy=policy,
                                  experimental_binary=(pins[1], pins[2]) if kind == 'balanced2' else None)
                    row = {'kind': kind, 'version': lab.version, 'policy': policy, 'success': False}
                    report['rows'].append(row)
                    try:
                        lab.dc.socket_timeout = 25
                        for task in Path(f'/proc/{lab.telemt.pid}/task').iterdir():
                            try: os.sched_setaffinity(int(task.name), {backend_cpu})
                            except ProcessLookupError: pass
                        command(['tc','qdisc','add','dev','lo','root','handle','1:','prio'])
                        command(['tc','qdisc','add','dev','lo','parent','1:3','handle','30:','netem','delay','3500ms','limit','1000'])
                        command(['tc','filter','add','dev','lo','protocol','ip','parent','1:','prio','1','u32',
                                 'match','ip','protocol','6','0xff','match','ip','dport',str(lab.dc.server_address[1]),'0xffff',
                                 'match','u8','0x02','0x02','at','33','flowid','1:3'])
                        try:
                            row['transfer'] = lab.transfer('apple.com', 'D', 4096, 12500000, persistent=True, io_timeout=25)
                            row['success'] = True
                            lab.validate(1)
                        except Exception as error:
                            row['failure'] = {'type': type(error).__name__, 'phase': getattr(error,'lab_phase','unknown'),
                                'reason': sanitize_startup_log(str(error)), 'elapsed_seconds': getattr(error,'lab_elapsed_seconds',None)}
                        row['core_alive'] = lab.telemt.poll() is None
                        row['config_sha256'] = hashlib.sha256(lab.telemt_config.read_bytes()).hexdigest()
                    finally:
                        subprocess.run(['tc','qdisc','del','dev','lo','root'],capture_output=True,timeout=10)
                        lab.close()
                        row['cleaned'] = not lab.root.exists() and all(p.poll() is not None for p in lab.processes)
                    write(Path(args.output), report)
        report['accepted'] = (len(report['rows']) == 4 and all(r['core_alive'] and r['cleaned'] and
            r['success'] == (r['policy']=='bounded-retry') and
            (r['success'] or r.get('failure',{}).get('phase')=='payload') for r in report['rows']))
    finally:
        report['resources_after'] = resources()
        report['oom_delta'] = {k: report['resources_after']['memory_events'][k]-report['resources_before']['memory_events'][k] for k in ('oom','oom_kill')}
        report['accepted'] = report['accepted'] and not any(report['oom_delta'].values())
        write(Path(args.output), report)
    return 0 if report['accepted'] else 1


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    raise SystemExit(main())
