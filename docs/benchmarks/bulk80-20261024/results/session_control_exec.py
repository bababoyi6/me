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
ROOT = BASE / 'tdh-audit-bulk80-20261024'
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


def setup(source_sha, pair_sha):
    assert digest(BASE/'bulk80-source-20261024.tar.gz') == source_sha
    assert digest(BASE/'bulk80-pair.tar.gz') == pair_sha
    ROOT.mkdir(mode=0o700)
    (ROOT/'results').mkdir(mode=0o700)
    project = ROOT/'project'
    project.mkdir(mode=0o700)
    unpack(BASE/'bulk80-source-20261024.tar.gz', project, 32*1024**2)
    state = json.loads((project/'SOURCE_STATE.json').read_text())
    for name, expected in state['source_sha256'].items():
        assert digest(project/name) == expected
    build = ROOT/'build'
    build.mkdir(mode=0o700)
    unpack(BASE/'bulk80-pair.tar.gz', build, 128*1024**2)
    manifest = json.loads((build/'manifest.json').read_text())
    assert manifest['source_commit'] == 'ac71d92ec41dea00a7eafd6b8d350c3486633500'
    assert manifest['project_commit'] == '02ec71320c831735b025107f5b2181097657ac96'
    assert manifest['candidate_helper_sha256'] == digest(project/'patches/unsent_queue_bulk.rs')
    assert manifest['base_patch_sha256'] == digest(project/'patches/telemt-3.5.5-relay-backpressure.patch')
    for name, version in (('fixed', 'telemt 3.5.5+tdh.audit1'), ('bulk', 'telemt 3.5.5+tdh.balanced2')):
        metadata = manifest['binaries'][name]
        assert metadata['version'] == version and metadata['sha256'] == digest(build/('telemt-'+name))
        shutil.copyfile(build/('telemt-'+name), ROOT/('telemt-'+name))
        (ROOT/('telemt-'+name)).chmod(0o755)
        (ROOT/(name+'-build.json')).write_text(json.dumps(metadata)+'\n')
    prior_source=BASE/'bulk80-prior32.json'
    assert digest(prior_source)=='ea063da5aafeb0e6a47a5149b3c576fd10b080703e8a7ab3622a06ed181668ca'
    prior=ROOT/'run-tdh-audit-8c221e023eba'
    prior.mkdir(mode=0o700)
    shutil.copyfile(prior_source,prior/'supervisor.json')
    (prior/'supervisor.json').chmod(0o600)
    (ROOT/'results/imported-capacity-reference.json').write_text(json.dumps({'scope':'historical capacity evidence only; not a new workload','original_run':prior.name,'sha256':digest(prior_source),'original_export_sha256':'40f34099f45a79845635084bdc678729c538ad70fb6894e15fe6652e4febcb03'})+'\n')
    shutil.copyfile(Path(__file__),ROOT/'results/session_control.py')
    report = inventory()
    assert report['budget']['eligible'] and report['kernel_oom_kill_count'] is not None
    assert not report['existing_lab_namespaces']
    (ROOT/'preflight.json').write_text(json.dumps(report, indent=2)+'\n')
    (ROOT/'results/core-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps({'setup': True, 'budget': report['budget'], 'source_commit': state['base_commit']}))


def run(arguments):
    assert arguments==['32'], 'one explicitly authorized 80-percent-soft-policy batch only'
    prior=ROOT/'run-tdh-audit-8c221e023eba'
    assert [p.name for p in ROOT.glob('run-tdh-audit-*')]==[prior.name]
    assert not list((ROOT/'results').glob('ab-*.json'))
    allowed=['--min-streams','32','--max-streams','32','--payload-mib','256','--rate-mbps','1000','--rounds','2','--socket-details','--share-core-file','--client-send-buffer','262144','--previous-run',prior.name,'--memory-high-percent','80']
    argv=[sys.executable,'-B',str(ROOT/'project/tools/lab_runner.py'),'--root',str(ROOT),'--haproxy',str(RUNTIME/'usr/sbin/haproxy'),'--wg',str(RUNTIME/'usr/bin/wg'),'--profile','ab','--core-pair','bulk-fixed','--execute',*allowed]
    os.umask(0o077)
    with (ROOT/'results/authorized-once-32.json').open('x') as marker:
        marker.write(json.dumps({'scope':'one two-round fixed/bulk batch; explicit80percent soft threshold, unchanged hard limits; admission by sole runner before any workload','arguments':argv,'launcher':'exec replaces helper; no waiting duplicate Python process'})+'\n')
    shutil.copyfile(Path(__file__),ROOT/'results/session_control_exec.py')
    log=ROOT/'results'/('supervisor-'+str(time.time_ns())+'.log')
    with log.open('x') as output:
        os.dup2(output.fileno(),1)
        os.dup2(output.fileno(),2)
    os.execv(sys.executable,argv)


def report():
    sys.path.insert(0, str(ROOT/'project/tools'))
    from analyze_upload_ab import summarize
    rows = []
    for path in sorted((ROOT/'results').glob('ab-*.json')):
        value = json.loads(path.read_text())
        if 'classification' in value:
            rows.append({'file': path.name, 'payload_mib': value['payload_bytes_total_per_trial']//1024**2,
                         'summary': summarize(value)})
    supervisors = []
    for directory in sorted(ROOT.glob('run-tdh-audit-*')):
        if directory.name=='run-tdh-audit-8c221e023eba':
            continue
        value = json.loads((directory/'supervisor.json').read_text())
        samples = value['samples']
        supervisors.append({'run': directory.name, **{k:value.get(k) for k in ('exit','stop_reason','cleanup_verified','host_oom_kill_delta')},
            'max_memory_sampled': max((p.get('memory.current') or 0 for p in samples), default=0),
            'memory_high_events': max((p.get('memory.events',{}).get('high',0) for p in samples), default=0)})
    result = {'batches': rows, 'supervisors': supervisors}
    (ROOT/'results/summary.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result))


def export():
    files = [ROOT/'preflight.json', ROOT/'project/SOURCE_STATE.json',
             *sorted((ROOT/'results').iterdir()),
             *[p for directory in sorted(ROOT.glob('run-tdh-audit-*')) for p in sorted(directory.iterdir())]]
    destination = BASE/'bulk80-results-20261024.tar.gz'
    with tarfile.open(destination, 'x:gz') as archive:
        for path in files:
            assert path.is_file() and not path.is_symlink() and path.stat().st_size <= 32*1024**2
            archive.add(path, arcname=path.relative_to(ROOT), recursive=False)
    print(json.dumps({'sha256': digest(destination), 'bytes': destination.stat().st_size, 'files': len(files)}))


def cleanup(expected):
    assert digest(BASE/'bulk80-results-20261024.tar.gz') == expected
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
    for name in ('bulk80-source-20261024.tar.gz','bulk80-pair.tar.gz','bulk80-results-20261024.tar.gz','bulk80_lab.py','bulk80-prior32.json'):
        path = BASE/name
        assert not path.is_symlink()
        path.unlink()
    result = {'date_utc': datetime.now(timezone.utc).isoformat(), 'export_sha256': expected,
              'removed_owned_root': str(ROOT), 'lab_namespaces_remaining': [], 'lab_units_remaining': [],
              'lab_temporary_directories_remaining': [], 'lab_keys_remaining': [], 'listeners_unchanged': True,
              'host_oom_kill_delta': 0, 'historical_binaries_unchanged': True,
              'host_kernel_firewall_ssh_configuration_modified': False, 'production_nodes_accessed': False}
    (BASE/'bulk80-cleanup-20261024.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__ == '__main__':
    operation = sys.argv[1]
    if operation == 'setup': setup(*sys.argv[2:])
    elif operation == 'run': run(sys.argv[2:])
    elif operation == 'report': report()
    elif operation == 'export': export()
    elif operation == 'cleanup': cleanup(sys.argv[2])
    else: raise SystemExit('unknown operation')


