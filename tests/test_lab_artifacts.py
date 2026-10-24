import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from lab_artifacts import MARKER, cleanup_artifacts, register_artifact


@unittest.skipUnless(sys.platform=='linux','Linux lab temporary directories')
class ArtifactCleanupTests(unittest.TestCase):
    def setUp(self):
        self.parent=tempfile.TemporaryDirectory(prefix='tdh-ledger-test-')
        self.addCleanup(self.parent.cleanup)
        self.registry=Path(self.parent.name)/'records'

    def temporary(self):
        path=Path(tempfile.mkdtemp(prefix='tdh-e2e-',dir='/tmp'))
        self.addCleanup(lambda:shutil.rmtree(path,ignore_errors=True))
        return path

    def test_real_sigkill_leaves_registered_directory_for_supervisor(self):
        module_path=str(Path(__file__).resolve().parent)
        script='\n'.join(('import sys,os,signal,tempfile','from pathlib import Path',
            'sys.path.insert(0,sys.argv[1])','from lab_artifacts import register_artifact',
            "p=Path(tempfile.mkdtemp(prefix='tdh-e2e-',dir='/tmp'))",'register_artifact(Path(sys.argv[2]),p)',
            'print(str(p),flush=True)','os.kill(os.getpid(),signal.SIGKILL)'))
        child=subprocess.run([sys.executable,'-c',script,module_path,str(self.registry)],capture_output=True,text=True,timeout=5)
        self.assertEqual(child.returncode,-9)
        path=Path(child.stdout.strip())
        self.addCleanup(lambda:shutil.rmtree(path,ignore_errors=True))
        self.assertTrue(path.exists())
        result=cleanup_artifacts(self.registry,True)
        self.assertEqual(result['removed'],[path.name])
        self.assertFalse(result['errors'])
        self.assertFalse(path.exists())

    def test_live_process_token_change_and_unrelated_directory_are_not_deleted(self):
        path=self.temporary()
        unrelated=self.temporary()
        register_artifact(self.registry,path)
        result=cleanup_artifacts(self.registry,False)
        self.assertTrue(result['errors'])
        self.assertTrue(path.exists())
        (path/MARKER).write_text('different-token')
        self.assertTrue(cleanup_artifacts(self.registry,True)['errors'])
        self.assertTrue(path.exists())
        self.assertTrue(unrelated.exists())

    def test_forged_outside_path_and_symlinked_marker_fail_closed(self):
        path=self.temporary()
        register_artifact(self.registry,path)
        record=next(self.registry.glob('*.json'))
        data=json.loads(record.read_text())
        data['path']=self.parent.name
        record.write_text(json.dumps(data))
        self.assertTrue(cleanup_artifacts(self.registry,True)['errors'])
        data['path']=str(path)
        record.write_text(json.dumps(data))
        (path/MARKER).unlink()
        (path/MARKER).symlink_to(record)
        self.assertTrue(cleanup_artifacts(self.registry,True)['errors'])
        self.assertTrue(path.exists())
