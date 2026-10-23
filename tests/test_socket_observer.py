"""Keep the load observer scoped to Telemt rather than every test socket."""
import ast
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class SocketObserverTests(unittest.TestCase):
    def test_query_is_kernel_filtered_and_results_exclude_unrelated_processes(self):
        tree=ast.parse(Path(__file__).with_name('high_rtt_worker.py').read_text())
        tree.body=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='observe']
        command=Mock(return_value='0 0 127.0.0.1:32001 127.0.0.1:50000 users:(("telemt",pid=123,fd=5))\n'
                                  ' skmem:(r128,rb425984,t0,tb425984,f0,w0)\n'
                                  ' cubic rtt:100.1/1.2\n'
                                  '0 0 127.0.0.1:1234 127.0.0.1:1235 users:(("other",pid=999,fd=6))\n')
        env={'subprocess':SimpleNamespace(check_output=command),'re':re,
             'lab':SimpleNamespace(listen=32001,telemt=SimpleNamespace(pid=123))}
        exec(compile(tree,'socket-observer','exec'),env)
        rows=env['observe']()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['role'],'client')
        self.assertEqual(rows[0]['rb'],425984)
        self.assertEqual(command.call_args.args[0][-1],'( sport = :32001 or dport = :26000 )')
        self.assertEqual(command.call_args.kwargs['timeout'],3)
