"""Session helper for one owned, bounded native-core experiment directory."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time

BASE = Path('/root/tdh-audit-20261021')
ROOT = BASE / 'tdh-audit-path-20261024'
RUNTIME = BASE / 'runtime'
os.environ['PATH'] = str(RUNTIME/'usr/sbin') + ':' + str(RUNTIME/'usr/bin') + ':' + os.environ['PATH']
os.environ['LD_LIBRARY_PATH'] = str(RUNTIME/'usr/lib/x86_64-linux-gnu')


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(65536), b''):
            value.update(block)
    return value.hexdigest()


def unpack(archive, destination, limit):
    with tarfile.open(archive) as source:
        entries = source.getmembers()
        assert all(p.isfile() and not Path(p.name).is_absolute() and '..' not in Path(p.name).parts for p in entries)
        assert len({p.name for p in entries}) == len(entries)
        assert sum(p.size for p in entries) <= limit
        source.extractall(destination)


def inventory():
    sys.path.insert(0, str(ROOT/'project/tools'))
    from lab_preflight import inventory as inspect
    return inspect(memory_high_percent=80)


BUNDLE_SHA = 'b4fec3efd10288a9f211ad958337fa314a15486e72fd5c50ab870f22a19737e5'
CORE_SHA = '5dc5d9b5ac43a0f12a402159e127b3b69e18ccb355c99b0322e73d870d907100'
VERSION = '3.5.5+tdh.balanced2'


def setup(source_sha):
    os.umask(0o077)
    assert digest(BASE/'path-source-20261024.tar.gz') == source_sha
    assert digest(BASE/'path-candidate.tar.gz') == BUNDLE_SHA
    assert not ROOT.exists() and not ROOT.is_symlink()
    ROOT.mkdir(mode=0o700)
    (ROOT/'results').mkdir(mode=0o700)
    project = ROOT/'project'
    project.mkdir(mode=0o700)
    unpack(BASE/'path-source-20261024.tar.gz', project, 32*1024**2)
    state = json.loads((project/'SOURCE_STATE.json').read_text())
    for name, expected in state['source_sha256'].items():
        assert digest(project/name) == expected
    sys.path.insert(0, str(project/'tools'))
    from core_install import archive_payload, durable_file
    binary, documents = archive_payload(BASE/'path-candidate.tar.gz', BUNDLE_SHA, VERSION, CORE_SHA, True)
    durable_file(ROOT/'telemt-bulk', binary, 0o755)
    notices = ROOT/'core-source-notice'
    notices.mkdir(mode=0o700)
    for name, data in documents.items():
        durable_file(notices/name, data, 0o644)
    metadata = {'sha256': CORE_SHA, 'version': 'telemt '+VERSION, 'bundle_sha256': BUNDLE_SHA}
    (ROOT/'bulk-build.json').write_text(json.dumps(metadata)+'\n')
    shutil.copyfile(Path(__file__),ROOT/'results/session_control.py')
    report = inventory()
    assert report['budget']['eligible'] and report['kernel_oom_kill_count'] is not None
    assert report['cpus']==1 and report['architecture']=='x86_64'
    assert report['memory_bytes']['SwapTotal']==0
    assert not report['existing_lab_namespaces']
    assert not subprocess.check_output(['systemctl','list-units','--all','--plain','--no-legend','tdh-audit-*'],text=True,timeout=10).strip()
    (ROOT/'preflight.json').write_text(json.dumps(report, indent=2)+'\n')
    (ROOT/'results/core-identity.json').write_text(json.dumps(metadata, indent=2)+'\n')
    print(json.dumps({'setup':True,'budget':report['budget'],'source_commit':state['base_commit']}))


def run(arguments):
    assert arguments==[], 'one explicitly authorized bounded candidate path only'
    assert not list(ROOT.glob('run-tdh-audit-*'))
    assert not (ROOT/'results/candidate-path-bulk.json').exists()
    assert digest(ROOT/'telemt-bulk')==CORE_SHA
    argv=[sys.executable,'-B',str(ROOT/'project/tools/lab_runner.py'),'--root',str(ROOT),
          '--haproxy',str(RUNTIME/'usr/sbin/haproxy'),'--wg',str(RUNTIME/'usr/bin/wg'),
          '--profile','candidate','--rtt','100','--memory-high-percent','80','--execute']
    os.umask(0o077)
    with (ROOT/'results/authorized-once-path.json').open('x') as marker:
        marker.write(json.dumps({'scope':'one high-RTT candidate path; <=2MiB payload; 180s group deadline; 80percent soft policy; unchanged hard limits; no retry','arguments':argv,'launcher':'exec replaces helper'})+'\n')
    log=ROOT/'results'/('supervisor-'+str(time.time_ns())+'.log')
    with log.open('x') as output:
        os.dup2(output.fileno(),1)
        os.dup2(output.fileno(),2)
    os.execv(sys.executable,argv)


def report():
    supervisors=[]
    for directory in sorted(ROOT.glob('run-tdh-audit-*')):
        value=json.loads((directory/'supervisor.json').read_text())
        samples=value['samples']
        supervisors.append({'run':directory.name,**{k:value.get(k) for k in ('exit','stop_reason','cleanup_verified','host_oom_kill_delta')},
            'max_memory_sampled':max((p.get('memory.current') or 0 for p in samples),default=0),
            'memory_high_events':max((p.get('memory.events',{}).get('high',0) for p in samples),default=0)})
    path=ROOT/'results/candidate-path-bulk.json'
    outcome=json.loads(path.read_text()) if path.exists() else None
    result={'candidate_path':outcome,'supervisors':supervisors}
    (ROOT/'results/summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


def export():
    files = [ROOT/'preflight.json', ROOT/'project/SOURCE_STATE.json',
             *sorted((ROOT/'results').iterdir()),
             *[p for directory in sorted(ROOT.glob('run-tdh-audit-*')) for p in sorted(directory.iterdir())]]
    destination = BASE/'path-results-20261024.tar.gz'
    with tarfile.open(destination, 'x:gz') as archive:
        for path in files:
            assert path.is_file() and not path.is_symlink() and path.stat().st_size <= 32*1024**2
            archive.add(path, arcname=path.relative_to(ROOT), recursive=False)
    print(json.dumps({'sha256': digest(destination), 'bytes': destination.stat().st_size, 'files': len(files)}))


def cleanup(expected):
    assert digest(BASE/'path-results-20261024.tar.gz') == expected
    assert ROOT.resolve() == ROOT and not ROOT.is_symlink() and ROOT.stat().st_uid == 0
    assert not ROOT.stat().st_mode & 0o077
    def checked(*argv):
        return subprocess.check_output(argv, text=True, timeout=10).strip()
    assert not any(line.startswith('tdh-audit-') for line in checked('ip','netns','list').splitlines())
    assert not checked('systemctl','list-units','--all','--plain','--no-legend','tdh-audit-*')
    assert not [p for p in Path('/tmp').glob('tdh-*') if p.is_dir()]
    assert not list(ROOT.glob('*.key'))
    before = json.loads((ROOT/'preflight.json').read_text())
    after = inventory()
    assert sorted(before['listeners'], key=str) == sorted(after['listeners'], key=str)
    assert before['kernel_oom_kill_count'] == after['kernel_oom_kill_count']
    historical = {'telemt-auto': '2e5f864461be842dbca2ee8638f3b68327f56d5f1666ea0f2622a491b2ba75d1',
                  'telemt-flush': 'e0aa14b4032bbfa32fd5389e815371008a78aeae39ec099f9b850e2db3160582'}
    assert all(digest(BASE/name) == value for name,value in historical.items())
    shutil.rmtree(ROOT)
    for name in ('path-source-20261024.tar.gz','path-candidate.tar.gz','path-results-20261024.tar.gz','path_lab.py'):
        path = BASE/name
        assert not path.is_symlink()
        path.unlink()
    result = {'date_utc': datetime.now(timezone.utc).isoformat(), 'export_sha256': expected,
              'removed_owned_root': str(ROOT), 'lab_namespaces_remaining': [], 'lab_units_remaining': [],
              'lab_temporary_directories_remaining': [], 'lab_keys_remaining': [], 'listeners_unchanged': True,
              'host_oom_kill_delta': 0, 'historical_binaries_unchanged': True,
              'host_kernel_firewall_ssh_configuration_modified': False, 'production_nodes_accessed': False}
    (BASE/'path-cleanup-20261024.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__ == '__main__':
    operation = sys.argv[1]
    if operation == 'setup': setup(*sys.argv[2:])
    elif operation == 'run': run(sys.argv[2:])
    elif operation == 'report': report()
    elif operation == 'export': export()
    elif operation == 'cleanup': cleanup(sys.argv[2])
    else: raise SystemExit('unknown operation')


