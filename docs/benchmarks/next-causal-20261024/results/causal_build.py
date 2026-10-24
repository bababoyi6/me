"""Compile only the tiny lab interposer, then verify it in a bounded private unit."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import uuid

base=Path('/root/tdh-audit-20261021')
root=base/'tdh-audit-causal-20261023'
project=root/'project'
(root/'results').mkdir(mode=0o700,exist_ok=True)
if sys.argv[1:] == ['--worker']:
    compiler=base/'runtime/usr/bin/tcc'
    source=project/'tests/socket_policy_lab.c'
    library=root/'socket-policy.so'
    subprocess.run([str(compiler),'-nostdlib','-shared','-o',str(library),str(source)],check=True,timeout=10)
    library.chmod(0o644)
    result=subprocess.check_output(['python3',str(project/'tests/policy_oracle.py'),'--library',str(library)],text=True,timeout=15)
    (root/'results/policy-oracle.json').write_text(result)
    metadata={'sha256':hashlib.sha256(library.read_bytes()).hexdigest(),
              'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
              'compiler_sha256':hashlib.sha256(compiler.read_bytes()).hexdigest(),
              'scope':'lab-only dynamic interposer; no Rust core changes',
              'oracle_complete':json.loads(result)['complete']}
    (root/'socket-policy-build.json').write_text(json.dumps(metadata,indent=2)+'\n')
    print(json.dumps(metadata))
else:
    sys.path.insert(0,str(project/'tools'))
    from lab_preflight import inventory
    from lab_runner import systemd_command
    report=inventory()
    assert report['budget']['eligible']
    unit='tdh-audit-policy-build-'+uuid.uuid4().hex[:12]
    argv=systemd_command(unit,report['budget'],['python3',__file__,'--worker'],30)
    argv.insert(argv.index('--'),'--property=PrivateNetwork=yes')
    result=subprocess.run(argv,capture_output=True,text=True,timeout=55)
    (root/'results/policy-build.log').write_text(result.stdout+result.stderr)
    (root/'results/policy-build-control.json').write_text(json.dumps({'unit':unit,'exit':result.returncode,'command':argv},indent=2)+'\n')
    subprocess.run(['systemctl','stop',unit],capture_output=True,timeout=10)
    subprocess.run(['systemctl','reset-failed',unit],capture_output=True,timeout=10)
    print(json.dumps({'exit':result.returncode,'output':result.stdout+result.stderr}))
