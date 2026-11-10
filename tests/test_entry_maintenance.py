import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from entry_maintenance import maintain, MaintenanceError


class EntryMaintenanceTests(unittest.TestCase):
    def test_drain_requires_healthy_peer_on_both_lines(self):
        calls = []
        def request(command):
            calls.append(command)
            return '# pxname,svname,status\ntelemt_a,vps2,UP\ntelemt_b,vps2,UP\n' if command == 'show stat' else ''
        self.assertEqual(maintain('drain', '1', request), 'drain')
        self.assertEqual(calls[1:], ['set server telemt_a/vps1 state drain', 'set server telemt_b/vps1 state drain'])
        for reply in ('', '# pxname,svname,status\ntelemt_a,vps2,DOWN\n',
                      '# pxname,svname,status\ntelemt_a,vps2,UP\ntelemt_b,vps2,DRAIN\n'):
            with self.assertRaises(RuntimeError): maintain('drain', '1', lambda command: reply)

    def test_arbitrary_inputs_never_reach_socket(self):
        def never(command): raise AssertionError(command)
        for action, index in (('drain', '1\nshutdown sessions server'), ('stop', '2'), ('resume', '3')):
            with self.assertRaises(ValueError): maintain(action, index, never)

    def test_resume_and_rejected_runtime_command(self):
        self.assertEqual(maintain('resume', '2', lambda command: ''), 'ready')
        with self.assertRaises(RuntimeError): maintain('resume', '2', lambda command: 'No such server')

    def test_single_file_entry_control_matches_reviewed_source(self):
        root = Path(__file__).resolve().parents[1]
        script = (root/'telemt-dual-hop.sh').read_text(encoding='utf-8')
        embedded = script.split('# BEGIN GENERATED ENTRY MAINTENANCE\n')[1].split('# END GENERATED ENTRY MAINTENANCE\n')[0]
        self.assertEqual(embedded, (root/'tools/entry_maintenance.py').read_text(encoding='utf-8'))

    def test_partial_change_has_fixed_actionable_message_without_echoing_reply(self):
        calls=[]
        def request(command):
            calls.append(command)
            return '' if len(calls)==1 else 'SYNTHETIC_PRIVATE_REPLY'
        with self.assertRaises(MaintenanceError) as caught:
            maintain('resume','1',request)
        self.assertEqual(len(calls),2)
        self.assertIn('a status',str(caught.exception))
        self.assertIn('只有一条',str(caught.exception))
        self.assertNotIn('SYNTHETIC_PRIVATE_REPLY',str(caught.exception))
