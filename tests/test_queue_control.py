"""Ownership/transaction regressions; real kernel and WG hooks have a bounded lab."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from queue_control import KernelQueue, QueueControl, QueueError, policy, encoded, durable, read_file


class QueuePolicyTests(unittest.TestCase):
    def test_no_entry_oversubscription_or_implicit_link_guess(self):
        for interface,rate,capacity in (('tdh1',180,200),('tdh2',90,200),('tdh-b1',True,200),
            ('tdh-b1',180,180),('tdh-b1',191,200),('tdh-b1',901,1000),('tdh-b1',180,None)):
            with self.subTest(interface=interface,rate=rate),self.assertRaises(QueueError):policy(interface,rate,capacity)
        self.assertEqual(policy('tdh-b1',180,200)['rate_mbps'],180)

    def test_active_drift_cannot_be_deleted_as_partial_install(self):
        kernel=KernelQueue('tdh-b1')
        with patch.object(kernel,'link',return_value={'txqlen':1000}),patch.object(kernel,'shape',
            return_value={'qdisc':['qdisc noqueue 0: root'],'class':[]}):
            with self.assertRaises(QueueError):kernel.restore(180,0,'a'*64)

    def test_fresh_noqueue_does_not_assume_zero_txqlen(self):
        kernel=KernelQueue('tdh-b1')
        for length in (0,1000,2000):
            with self.subTest(length=length),patch.object(kernel,'link',return_value={'txqlen':length}),\
                patch.object(kernel,'shape',return_value={'qdisc':['qdisc noqueue 0: root'],'class':[]}):
                self.assertTrue(kernel.fresh())


@unittest.skipUnless(os.geteuid()==0,'root-owned state boundary')
class QueueStateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='tdh-queue-state-')
        self.root=Path(self.temp.name);self.root.chmod(0o700)
        self.manager=self.root/'manager';self.manager.write_bytes(b'# TDH_QUEUE_PROTOCOL=1\n'+b'#'*100000)
        self.manager.chmod(0o755)
        self.control=QueueControl(self.root/'queue','tdh-b1',self.manager,self.root/'dropin'/'90-telemt-queue.conf')

    def tearDown(self):self.temp.cleanup()

    def test_disabled_status_does_not_create_files_or_hooks(self):
        self.assertEqual(self.control.status(),{'enabled':False})
        self.assertFalse(self.control.directory.exists())
        self.assertFalse(self.control.dropin.exists())

    def test_ordinary_apply_failure_restores_disabled_persistence(self):
        with patch.object(self.control.kernel,'fresh',return_value=True),patch('queue_control.run'),\
            patch.object(self.control,'apply',side_effect=QueueError('injected real-apply rollback completed')):
            with self.assertRaises(QueueError):self.control.enable(180,200)
        self.assertFalse(self.control.config.exists());self.assertFalse(self.control.dropin.exists())

    def test_symlink_hardlink_and_world_readable_policy_refused(self):
        self.control.directory.mkdir(mode=0o700)
        target=self.root/'target';target.write_bytes(encoded(policy('tdh-b1',180,200)));target.chmod(0o600)
        self.control.config.symlink_to(target)
        with self.assertRaises((OSError,QueueError)):self.control.desired()
        self.control.config.unlink();os.link(target,self.control.config)
        with self.assertRaises(QueueError):self.control.desired()
        self.control.config.unlink();self.control.config.write_bytes(target.read_bytes());self.control.config.chmod(0o644)
        with self.assertRaises(QueueError):self.control.desired()
        self.assertEqual(target.read_bytes(),encoded(policy('tdh-b1',180,200)))

    def test_hook_failure_cannot_fail_tunnel_and_marker_blocks_incompatible_manager(self):
        self.assertIn(b'ExecStartPost=-',self.control.hook())
        self.assertIn(b'ExecStopPost=-',self.control.hook())
        self.assertEqual(read_file(self.manager,False,1048576),self.manager.read_bytes())
        self.manager.write_bytes(b'old manager\n')
        with self.assertRaises(QueueError):self.control.enable(180,200)
        self.assertFalse(self.control.directory.exists())

    def test_pending_disable_prevents_boot_reenable(self):
        with self.control.locked(True):
            durable(self.control.config,encoded(policy('tdh-b1',180,200)))
            durable(self.control.stopping,b'1\n')
        with patch.object(self.control,'apply',side_effect=AssertionError('must not apply')):
            self.assertEqual(self.control.service('apply'),'removal-pending')

    def test_manager_replacement_guard_preserves_legacy_when_feature_off(self):
        candidate=self.root/'old';candidate.write_text('old manager\n')
        script=Path(__file__).resolve().parents[1]/'telemt-dual-hop.sh'
        env=dict(os.environ,TDH_BASE=str(self.root),TDH_SOURCE_ONLY='1',TDH_TEST_SCRIPT=str(script),TDH_TEST_CANDIDATE=str(candidate))
        args=['bash','-c','source "$TDH_TEST_SCRIPT"; queue_manager_guard "$TDH_TEST_CANDIDATE"']
        self.assertEqual(subprocess.run(args,env=env,capture_output=True,timeout=5).returncode,0)
        self.control.directory.mkdir(mode=0o700);self.control.config.write_bytes(encoded(policy('tdh-b1',180,200)))
        self.assertNotEqual(subprocess.run(args,env=env,capture_output=True,timeout=5).returncode,0)
