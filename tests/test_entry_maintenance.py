import concurrent.futures
from pathlib import Path
import shutil
import signal
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from entry_maintenance import maintain, MaintenanceError, migrate_health, tracked_health_config, wait_runtime

LEGACY = b'''backend telemt_a
    server vps1 10.77.1.2:24431 check port 19101 inter 2s fall 3 rise 2 send-proxy-v2 agent-check agent-port 19101 agent-inter 2s
    server vps2 10.77.2.2:24431 check port 19101 inter 2s fall 3 rise 2 send-proxy-v2 agent-check agent-port 19101 agent-inter 2s backup

backend telemt_b
    server vps2 10.77.2.2:24431 check port 19101 inter 2s fall 3 rise 2 send-proxy-v2 agent-check agent-port 19101 agent-inter 2s
    server vps1 10.77.1.2:24431 check port 19101 inter 2s fall 3 rise 2 send-proxy-v2 agent-check agent-port 19101 agent-inter 2s backup
'''


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

    def test_health_migration_preserves_unrelated_config_and_rejects_drift(self):
        original = b'global\n    maxconn 2048\n\n' + LEGACY + b'\nbackend other\n    timeout connect 1s\n'
        updated = tracked_health_config(original)
        self.assertTrue(updated.startswith(b'global\n    maxconn 2048\n\n'))
        self.assertTrue(updated.endswith(b'\nbackend other\n    timeout connect 1s\n'))
        self.assertEqual(updated.count(b' track telemt_health/'), 4)
        for changed in (updated, original.replace(b'inter 2s', b'inter 3s', 1)):
            with self.assertRaises(MaintenanceError):
                tracked_health_config(changed)

    @unittest.skipUnless(shutil.which('haproxy'), 'requires HAProxy')
    def test_health_migration_reloads_and_can_restore_original(self):
        from lab import Lab

        lab = Lab(shutil.which('haproxy'), master=True)
        self.addCleanup(lab.close)
        original_lab = (lab.root/'haproxy.cfg').read_text()
        prefix = original_lab.split('\nbackend telemt_a\n', 1)[0] + '\n'
        path = lab.root/'managed.cfg'
        path.write_bytes(b'global\n    maxconn 2048\n\ndefaults\n    mode tcp\n'
                         b'    timeout connect 1s\n    timeout client 15s\n    timeout server 15s\n\n'+LEGACY)
        original = path.read_bytes()
        path.chmod(0o640)
        backup = path.with_name(path.name+'.health-previous')
        reloads = []

        def reload():
            reloads.append(path.read_bytes())
            content = path.read_text().split('backend ',1)[1]
            content = 'backend ' + content
            for index, (backend, agent) in enumerate(zip(lab.backends,lab.agents),1):
                port = str(agent.server_address[1])
                content = (content.replace(f'10.77.{index}.2:24431', f'127.0.0.1:{backend.server_address[1]}')
                           .replace(f'10.77.{index}.2:19101', f'127.0.0.1:{port}'))
                content = '\n'.join(line.replace('port 19101', f'port {port}')
                                    if f'127.0.0.1:{backend.server_address[1]} ' in line else line
                                    for line in content.split('\n'))
            (lab.root/'haproxy.cfg').write_text(prefix+content)
            lab.process.send_signal(signal.SIGUSR2)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            active = pool.submit(lab.transfer,'apple.com','D',2*1024**2,1024**2)
            time.sleep(.2)
            self.assertEqual(migrate_health(path,False,lab.stat,reload), 'applied')
            self.assertEqual(active.result(timeout=8)[0], '1')
        self.assertEqual(backup.read_bytes(), original)
        self.assertEqual(path.stat().st_mode & 0o777, 0o640)
        self.assertEqual(migrate_health(path,False,lab.stat,reload), 'unchanged')
        self.assertEqual(len(reloads),1)
        self.assertEqual(lab.transfer('apple.com','D',1024,0)[0],'1')
        self.assertEqual(lab.transfer('gs.apple.com','D',1024,0)[0],'2')
        self.assertEqual(migrate_health(path,True,lab.stat,reload), 'rolled-back')
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(len(reloads),2)
        def fail_migration(request, tracked, *, require_ready):
            if tracked:
                raise MaintenanceError('injected health timeout')
            return wait_runtime(request, tracked, require_ready=require_ready)

        with patch('entry_maintenance.wait_runtime', side_effect=fail_migration):
            with self.assertRaisesRegex(MaintenanceError, '已恢复'):
                migrate_health(path,False,lab.stat,reload)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(len(reloads),4)
