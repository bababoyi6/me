"""Run Python regressions on the Netherlands VPS, with resource and network limits."""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

root = Path('/root/tdh-audit-20261021/tdh-audit-continue-20261023')
sys.path.insert(0, str(root/'project/tools'))
from lab_preflight import inventory
from lab_runner import systemd_command
report = inventory()
assert report['budget']['eligible']
unit = 'tdh-audit-check-' + uuid.uuid4().hex[:12]
argv = systemd_command(unit, report['budget'],
                       ['python3','-m','unittest','discover','-s','tests','-p','test_*.py','-v'],120)
argv.insert(argv.index('--'), '--property=PrivateNetwork=yes')
for filename, content in (
        ('passwd', 'root:x:0:0:root:/root:/bin/bash\nnobody:x:65534:65534:nobody:/nonexistent:/usr/sbin/nologin\nhaproxy:x:65534:65534:Lab identity:/nonexistent:/usr/sbin/nologin\n'),
        ('group', 'root:x:0:\nnogroup:x:65534:\nhaproxy:x:65534:\n'),
        ('hosts', '127.0.0.1 localhost apple.com gs.apple.com\n::1 localhost\n')):
    fixture = root / (filename + '.fixture')
    fixture.write_text(content)
    fixture.chmod(0o600)
    argv.insert(argv.index('--'), '--property=BindReadOnlyPaths=' + str(fixture) + ':/etc/' + filename)
result = subprocess.run(argv,capture_output=True,text=True,timeout=150)
destination=root/'results'
destination.mkdir(exist_ok=True)
(destination/'python-tests.txt').write_text(result.stdout+result.stderr)
record = {'classification':'REMOTE_UNIT_TEST','exit':result.returncode,'preflight':report,
          'unit':unit,'command':argv,'output':'python-tests.txt'}
for prop in ('Result','ExecMainStatus','MemoryCurrent','CPUUsageNSec'):
    record[prop]=subprocess.run(['systemctl','show',unit,'--property='+prop,'--value'],
                                capture_output=True,text=True,timeout=5).stdout.strip()
subprocess.run(['systemctl','stop',unit],capture_output=True,timeout=10)
subprocess.run(['systemctl','reset-failed',unit],capture_output=True,timeout=10)
(destination/'python-tests.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps({'exit':result.returncode,'tail':(result.stdout+result.stderr)[-5000:]}))
