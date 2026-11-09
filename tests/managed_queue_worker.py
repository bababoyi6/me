"""Invoke the exact controller only on the performance lab's renamed backend WG."""
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from queue_control import QueueControl

if __name__=='__main__':
    root=Path(sys.argv[1]);action=sys.argv[2]
    if (os.geteuid()!=0 or os.environ.get('GITHUB_ACTIONS')!='true' or
        not root.name.startswith('tdh-audit-') or
        os.stat('/proc/self/ns/net').st_ino!=os.stat('/run/netns/tdh-audit-b').st_ino):
        raise RuntimeError('owned private backend namespace required')
    control=QueueControl(root/'managed-queue','tdh-b1',root/'managed-manager',root/'managed-systemd'/'90-telemt-queue.conf')
    if action=='enable':print(control.enable(180,200))
    elif action=='disable':print(control.disable())
    else:raise RuntimeError('unsupported lab action')
