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
ROOT = BASE / 'tdh-audit-grade-20261024'
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
    return inspect()


def setup(source_sha, pair_sha):
    assert digest(BASE/'grade-source-20261024.tar.gz') == source_sha
    assert digest(BASE/'grade-pair.tar.gz') == pair_sha
    ROOT.mkdir(mode=0o700)
    (ROOT/'results').mkdir(mode=0o700)
    project = ROOT/'project'
    project.mkdir(mode=0o700)
    unpack(BASE/'grade-source-20261024.tar.gz', project, 32*1024**2)
    state = json.loads((project/'SOURCE_STATE.json').read_text())
    for name, expected in state['source_sha256'].items():
        assert digest(project/name) == expected
    build = ROOT/'build'
    build.mkdir(mode=0o700)
    unpack(BASE/'grade-pair.tar.gz', build, 128*1024**2)
    manifest = json.loads((build/'manifest.json').read_text())
    assert manifest['source_commit'] == 'ac71d92ec41dea00a7eafd6b8d350c3486633500'
    assert manifest['project_commit'] == 'f6156a9ba3242215b4df60d118f28f53ee73fb8a'
    assert manifest['candidate_helper_sha256'] == digest(project/'patches/low_rtt_receive.rs')
    assert manifest['base_patch_sha256'] == digest(project/'patches/telemt-3.5.5-relay-backpressure.patch')
    for name, version in (('fixed', 'telemt 3.5.5+tdh.audit1'), ('balanced', 'telemt 3.5.5+tdh.balanced1')):
        metadata = manifest['binaries'][name]
        assert metadata['version'] == version and metadata['sha256'] == digest(build/('telemt-'+name))
        shutil.copyfile(build/('telemt-'+name), ROOT/('telemt-'+name))
        (ROOT/(name+'-build.json')).write_text(json.dumps(metadata)+'\n')
    report = inventory()
    assert report['budget']['eligible'] and report['kernel_oom_kill_count'] is not None
    assert not report['existing_lab_namespaces']
    (ROOT/'preflight.json').write_text(json.dumps(report, indent=2)+'\n')
    (ROOT/'results/core-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps({'setup': True, 'budget': report['budget'], 'source_commit': state['base_commit']}))


def gate16():
    batches = [json.loads(p.read_text()) for p in (ROOT/'results').glob('ab-*.json') if 'classification' in json.loads(p.read_text())]
    assert len(batches) == 1, '16-stage must have exactly one batch'
    batch = batches[0]
    assert batch['complete'] and len(batch['rows']) == 4
    ratios=[]
    for turn in (1,2):
        pair={p['build']: p for p in batch['rows'] if p['pair_round']==turn}
        assert set(pair)=={'fixed','balanced'}
        values={}
        for name,p in pair.items():
            assert p['streams']==16 and p['exit']==0
            r=p['trial']['rows'][0]
            assert r['complete'] and r['measurement_complete'] and r['sha256_verified_streams']==16
            assert r['small_probes'] and not r['small_probe_errors'] and not r['telemetry_errors'] and r['oom_delta']==0
            assert r['expected_payload_bytes']==128*1024**2
            values[name]=r['mbps']
        ratios.append(values['balanced']/values['fixed'])
    assert min(ratios)>=0.95, 'candidate below 95% of fixed reference in a paired round'
    runs=list(ROOT.glob('run-tdh-audit-*'))
    assert len(runs)==1
    supervisor=json.loads((runs[0]/'supervisor.json').read_text())
    assert supervisor['exit']==0 and supervisor['stop_reason'] is None and supervisor['cleanup_verified']
    assert supervisor['host_oom_kill_delta']==0
    current=inventory()
    assert current['budget']['eligible'] and not current['existing_lab_namespaces']
    print(json.dumps({'gate16_pass':True,'paired_speed_ratios':ratios}),flush=True)
    return ratios


def run(arguments):
    assert arguments[0] in ('16','32') and len(arguments)==1
    streams=int(arguments[0])
    allowed=['--min-streams',str(streams),'--max-streams',str(streams),'--payload-mib',str(streams*8),'--rate-mbps','1000','--rounds','2','--socket-details']
    if streams==32:
        gate16()
    with (ROOT/f'results/authorized-once-{streams}.json').open('x') as marker:
        marker.write(json.dumps({'scope':'one two-round fixed/balanced batch per tier; 32 only after all 16 gates; no retries','arguments':allowed})+'\n')
    arguments=allowed
    argv = [sys.executable, str(ROOT/'project/tools/lab_runner.py'), '--root', str(ROOT),
            '--haproxy', str(RUNTIME/'usr/sbin/haproxy'), '--wg', str(RUNTIME/'usr/bin/wg'),
            '--profile', 'ab', '--core-pair', 'balanced-fixed', '--execute', *arguments]
    log = ROOT/'results'/('supervisor-'+str(time.time_ns())+'.log')
    with log.open('x') as output:
        result = subprocess.run(argv, stdout=output, stderr=subprocess.STDOUT, timeout=630)
    print(json.dumps({'exit': result.returncode, 'log': log.name, 'tail': log.read_text()[-3000:]}))


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
    destination = BASE/'grade-results-20261024.tar.gz'
    with tarfile.open(destination, 'x:gz') as archive:
        for path in files:
            assert path.is_file() and not path.is_symlink() and path.stat().st_size <= 32*1024**2
            archive.add(path, arcname=path.relative_to(ROOT), recursive=False)
    print(json.dumps({'sha256': digest(destination), 'bytes': destination.stat().st_size, 'files': len(files)}))


def cleanup(expected):
    assert digest(BASE/'grade-results-20261024.tar.gz') == expected
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
    for name in ('grade-source-20261024.tar.gz','grade-pair.tar.gz','grade-results-20261024.tar.gz','grade_lab.py'):
        path = BASE/name
        assert not path.is_symlink()
        path.unlink()
    result = {'date_utc': datetime.now(timezone.utc).isoformat(), 'export_sha256': expected,
              'removed_owned_root': str(ROOT), 'lab_namespaces_remaining': [], 'lab_units_remaining': [],
              'lab_temporary_directories_remaining': [], 'lab_keys_remaining': [], 'listeners_unchanged': True,
              'host_oom_kill_delta': 0, 'historical_binaries_unchanged': True,
              'host_kernel_firewall_ssh_configuration_modified': False, 'production_nodes_accessed': False}
    (BASE/'grade-cleanup-20261024.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__ == '__main__':
    operation = sys.argv[1]
    if operation == 'setup': setup(*sys.argv[2:])
    elif operation == 'run': run(sys.argv[2:])
    elif operation == 'gate16': gate16()
    elif operation == 'report': report()
    elif operation == 'export': export()
    elif operation == 'cleanup': cleanup(sys.argv[2])
    else: raise SystemExit('unknown operation')


