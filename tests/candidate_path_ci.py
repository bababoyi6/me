"""Stage the pinned existing candidate for one bounded ephemeral-CI tunnel check."""
import argparse
import gc
import json
import os
from pathlib import Path
import sys

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/'tools'))
from candidate_bundle import CORE_SHA256, VERSION
from core_install import archive_payload,durable_file

BUNDLE_SHA256='b4fec3efd10288a9f211ad958337fa314a15486e72fd5c50ab870f22a19737e5'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True)
    parser.add_argument('--bundle',required=True)
    args=parser.parse_args()
    root=Path(args.root)
    temporary=Path(os.environ.get('RUNNER_TEMP','/nonexistent')).resolve()
    if (os.geteuid()!=0 or os.environ.get('GITHUB_ACTIONS')!='true' or
            not root.is_absolute() or root.resolve()!=root or root.exists() or root.is_symlink() or
            root.parent!=temporary or root.name!='tdh-audit-candidate-path'):
        parser.error('requires a fresh private root on the ephemeral CI runner')
    os.umask(0o077)
    binary,documents=archive_payload(args.bundle,BUNDLE_SHA256,VERSION,CORE_SHA256,True)
    root.mkdir(mode=0o700)
    (root/'results').mkdir(mode=0o700)
    durable_file(root/'telemt-bulk',binary,0o755)
    notices=root/'core-source-notice';notices.mkdir(mode=0o700)
    for name,content in documents.items():
        durable_file(notices/name,content,0o644)
    (root/'bulk-build.json').write_text(json.dumps({'sha256':CORE_SHA256,'version':'telemt '+VERSION})+'\n')
    del binary,documents
    gc.collect()
    # Replace the staging process. RuntimeMaxSec/CPU/memory/cleanup remain enforced by the runner.
    argv=[sys.executable,'-B',str(REPO/'tools/lab_runner.py'),'--root',str(root),
          '--profile','candidate','--rtt','100','--memory-high-percent','80','--execute']
    os.execv(sys.executable,argv)


if __name__=='__main__':
    main()
