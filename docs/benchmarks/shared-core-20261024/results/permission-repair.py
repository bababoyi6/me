"""Tighten an owned report without changing evidence; no workload is launched."""
import hashlib
import json
import shutil
from pathlib import Path
import share_lab as lab

root = lab.ROOT
runs = list(root.glob('run-tdh-audit-*'))
assert len(runs) == 1 and runs[0].name == 'run-tdh-audit-04a974917137'
assert not list((root/'results').glob('ab-*-32-*.json'))
report = runs[0]/'supervisor.json'
before = report.stat()
assert before.st_uid == 0 and before.st_mode & 0o777 == 0o644 and not report.is_symlink()
assert json.loads(report.read_text())['cleanup_verified'] is True
digest = hashlib.sha256(report.read_bytes()).hexdigest()
report.chmod(0o600)
assert hashlib.sha256(report.read_bytes()).hexdigest() == digest
for old, new in [('authorized-once-32.json', 'authorized-preflight-32.json'),
                 ('admission32.json', 'admission32-before-permission-repair.json')]:
    source, destination = root/'results'/old, root/'results'/new
    assert source.is_file() and not destination.exists()
    source.rename(destination)
record = {'scope':'permission repair only; 32-stream workload had not started',
          'relative_file':str(report.relative_to(root)), 'uid':0,
          'mode_before':'0644','mode_after':'0600','sha256_unchanged':digest,
          'preserved_preflight_log':'supervisor-1792836054551462532.log'}
(root/'results/permission-repair.json').write_text(json.dumps(record,indent=2)+'\n')
shutil.copyfile(Path(__file__),root/'results/permission-repair.py')
print(json.dumps(record))
