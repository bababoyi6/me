"""A post-load observer failure must not erase an already-completed transfer."""
import ast
import concurrent.futures
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from e2e_lab import close_on_error


class TrialRetentionTests(unittest.TestCase):
    def test_in_load_socket_failure_preserves_payload_and_invalidates_measurement(self):
        tree=ast.parse(Path(__file__).with_name('high_rtt_worker.py').read_text())
        tree.body=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='trial']
        future=Mock()
        future.done.side_effect=[False,True]
        future.result.return_value={'elapsed_seconds':.1}
        pool=Mock()
        pool.__enter__=Mock(return_value=pool)
        pool.__exit__=Mock(return_value=False)
        pool.submit.return_value=future
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'trial.json'
            args=SimpleNamespace(bytes=1024,observe_network=True,rate_mbps=100,slow_readers=False,
                                 small_probes=False,socket_observation='detailed',build='auto',dc_timeout=25,output=str(output))
            lab=SimpleNamespace(telemt=SimpleNamespace(pid=1),haproxy=SimpleNamespace(pid=2),version='synthetic',close=lambda:None,
                                transfer=lambda *a,**k:None)
            query=Mock(side_effect=OSError('synthetic netlink failure'))
            env=dict(args=args,lab=lab,time=SimpleNamespace(monotonic=time.monotonic,sleep=lambda _:None),
                     concurrent=SimpleNamespace(futures=SimpleNamespace(ThreadPoolExecutor=lambda **k:pool)),
                     close_on_error=close_on_error,ticks=lambda *a:1,rss=lambda:1024,oom=lambda:0,
                     memory_available=lambda:1024**3,cpu_snapshot=lambda:[],cpu_interval=lambda *a:None,
                     observe=query,network_counters=lambda:{},Path=Path,json=json,subprocess=subprocess,rows=[])
            exec(compile(tree,'retained-socket-trial','exec'),env)
            with contextlib.redirect_stdout(io.StringIO()),self.assertRaisesRegex(RuntimeError,'evidence retained'):
                env['trial'](0,1,'U')
            row=json.loads(output.read_text())['rows'][0]
            self.assertTrue(row['complete'])
            self.assertFalse(row['measurement_complete'])
            self.assertEqual(row['sha256_verified_streams'],1)
            self.assertEqual(row['telemetry_errors'],['OSError'])
            query.assert_called_once()

    def test_post_load_timeout_preserves_transfer_and_marks_measurement_invalid(self):
        tree=ast.parse(Path(__file__).with_name('high_rtt_worker.py').read_text())
        tree.body=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='trial']
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'trial.json'
            args=SimpleNamespace(bytes=1024,observe_network=True,rate_mbps=100,slow_readers=False,
                                 small_probes=False,socket_observation='off',build='auto',dc_timeout=25,output=str(output))
            lab=SimpleNamespace(telemt=SimpleNamespace(pid=1),haproxy=SimpleNamespace(pid=2),version='synthetic',
                                transfer=lambda *a,**k:{'elapsed_seconds':.1},close=lambda:None)
            env=dict(args=args,lab=lab,time=time,concurrent=concurrent,close_on_error=close_on_error,
                     ticks=lambda *a:1,rss=lambda:1024,oom=lambda:0,memory_available=lambda:1024**3,
                     cpu_snapshot=lambda:[],cpu_interval=lambda *a:None,observe=lambda:[],
                     network_counters=Mock(side_effect=[{},subprocess.TimeoutExpired('tc',3)]),
                     Path=Path,json=json,subprocess=subprocess,rows=[])
            exec(compile(tree,'retained-trial','exec'),env)
            with contextlib.redirect_stdout(io.StringIO()),self.assertRaisesRegex(RuntimeError,'evidence retained'):
                env['trial'](0,1,'U')
            row=json.loads(output.read_text())['rows'][0]
            self.assertTrue(row['complete'])
            self.assertFalse(row['measurement_complete'])
            self.assertEqual(row['sha256_verified_streams'],1)
            self.assertEqual(row['telemetry_errors'],['TimeoutExpired'])
