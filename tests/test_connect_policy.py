"""Config-only migration: exact preservation, private recovery and drift refusal."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from core_upgrade import bounded_connect_config, refresh_connect_policy
from core_transaction import CoreError


class ConnectPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.config=self.base/'telemt.toml'
        self.original=b'# keep\n[general]\nfast_mode = true\n[access.users]\nline_a = "SYNTHETIC_KEEP_SECRET"\n'
        self.config.write_bytes(self.original); self.config.chmod(0o640)
        class Service:
            def __init__(self,config):
                self.config=config; self.identity={'group':os.getegid()}; self.binary=Path('/synthetic/core'); self.work=config.parent
                self.starts=0; self.fail_new=False
            def stop(self): pass
            def start(self): self.starts+=1
            def ready(self): return not(self.fail_new and self.starts==1)
            def running_binary_matches(self): return True
        self.service=Service(self.config)
        self.cli=patch('core_upgrade.subprocess.run',return_value=SimpleNamespace(returncode=0))
        self.cli.start(); self.addCleanup(self.cli.stop)

    def call(self,rollback=False): return refresh_connect_policy(self.base,self.service,rollback)

    def test_migration_only_changes_three_defaults_and_is_idempotent(self):
        self.assertEqual(self.call(),'committed')
        new=self.config.read_bytes()
        for line in (b'tg_connect = 4\n',b'upstream_connect_retry_attempts = 2\n',b'upstream_connect_budget_ms = 9000\n'):
            self.assertIn(line,new); new=new.replace(line,b'')
        self.assertEqual(new,self.original)
        self.assertEqual(self.call(),'unchanged')
        self.assertEqual(self.service.starts,1)
        self.assertEqual(self.config.stat().st_mode & 0o777,0o640)
        self.assertEqual(self.config.stat().st_gid,os.getegid())
        backup=next((self.base/'connect-policy').glob('core.previous-*'))
        self.assertEqual(backup.read_bytes(),self.original)
        self.assertEqual(backup.stat().st_mode & 0o777,0o600)
        self.assertEqual(self.call(True),'recovered')
        self.assertEqual(self.config.read_bytes(),self.original)

    def test_custom_values_duplicate_keys_and_multiline_layout_refused(self):
        for extra in (b'tg_connect = 20\n',b'tg_connect = 10\ntg_connect = 10\n',b'tg_connect = "10"\n',b'custom = """x"""\n'):
            content=self.original.replace(b'[general]\n',b'[general]\n'+extra)
            with self.assertRaises(CoreError): bounded_connect_config(content)
        explicit=self.original.replace(b'[general]\n',b'[general]\ntg_connect = 10 # preserve comment\n')
        self.assertIn(b'tg_connect = 4 # preserve comment\n',bounded_connect_config(explicit))

    def test_failed_readiness_restores_exact_bytes_and_private_mode(self):
        self.service.fail_new=True
        with self.assertRaisesRegex(CoreError,'did not become ready'): self.call()
        self.assertEqual(self.config.read_bytes(),self.original)
        self.assertEqual(self.config.stat().st_mode & 0o777,0o640)

    def test_partial_transaction_requires_rollback_even_if_candidate_bytes_present(self):
        self.call()
        journal=self.base/'connect-policy/transaction.json'
        record=json.loads(journal.read_text()); record['phase']='switching'; journal.write_text(json.dumps(record))
        with self.assertRaisesRegex(CoreError,'unfinished'): self.call()
        self.call(True)
        self.assertEqual(self.config.read_bytes(),self.original)

    def test_external_secret_or_permission_changes_block_rollback(self):
        self.call()
        changed=self.config.read_bytes().replace(b'SYNTHETIC_KEEP_SECRET',b'CHANGED_SYNTHETIC_SECRET')
        self.config.write_bytes(changed)
        with self.assertRaisesRegex(CoreError,'outside this transaction'): self.call(True)
        self.assertEqual(self.config.read_bytes(),changed)
        self.config.chmod(0o644)
        with self.assertRaisesRegex(CoreError,'ownership or mode'): self.call(True)

    def test_config_edit_during_preflight_cannot_become_the_approved_baseline(self):
        changed=self.original.replace(b'SYNTHETIC_KEEP_SECRET',b'CONCURRENT_SYNTHETIC_SECRET')
        def editing_preflight(*args,**kwargs):
            self.config.write_bytes(changed)
            return SimpleNamespace(returncode=0)
        with patch('core_upgrade.subprocess.run',side_effect=editing_preflight):
            with self.assertRaisesRegex(CoreError,'approved baseline'): self.call()
        self.assertEqual(self.config.read_bytes(),changed)
        self.assertEqual(self.service.starts,0)
