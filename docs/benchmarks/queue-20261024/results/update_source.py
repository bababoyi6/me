"""Update owned lab source after preserving the failed control's exact identity."""
import json
import shutil
import subprocess
import queue_lab as lab

assert not subprocess.check_output(['ip','netns','list'],text=True).strip()
assert not subprocess.check_output(['systemctl','list-units','--all','--plain','--no-legend','tdh-audit-*'],text=True).strip()
source=lab.BASE/'queue-source-20261024.tar.gz'
assert lab.digest(source)=='e1fbdf30f670dfee88d193c7802648013626f0a1777986567fbce8d71150e81d'
previous=lab.BASE/'queue-results-20261024.tar.gz'
assert lab.digest(previous)=='17370ce4ebd16441df6a4f686e0408885963eb3fe3c95cb2c6e2b9c08d12c9ed'
destination=lab.BASE/'queue-results-before-candidate-20261024.tar.gz'
assert not destination.exists()
previous.rename(destination)
project=lab.ROOT/'project'
shutil.copyfile(project/'SOURCE_STATE.json',lab.ROOT/'results/source-state-before-candidate.json')
lab.unpack(source,project,32*1024**2)
state=json.loads((project/'SOURCE_STATE.json').read_text())
for name,digest in state['source_sha256'].items():
    assert lab.digest(project/name)==digest,name
print(json.dumps({'updated_source_commit':state['base_commit'],'files_verified':len(state['source_sha256'])}))
