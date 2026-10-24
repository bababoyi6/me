"""Resource plans and executable arguments must fail closed before privileged setup."""
import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import sys
import subprocess
import unittest
from unittest.mock import Mock, patch

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
from lab_preflight import budget, MIB, run_checked, escalation_budget
from lab_runner import systemd_command, cgroup_sample, namespace_cleanup_status, write_private_report, main as runner_main
sys.path.remove(str(TOOLS))


class LabRunnerTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform=='linux','Linux-only runner plan')
    def test_candidate_plan_is_tiny_high_rtt_private_and_time_bounded(self):
        output=io.StringIO()
        limits=budget(956*MIB,640*MIB,4096*MIB,memory_high_percent=80)
        argv=['lab_runner.py','--root','/tmp/tdh-audit-candidate-plan','--profile','candidate',
              '--rtt','100','--memory-high-percent','80']
        with patch('sys.argv',argv), patch('lab_runner.inventory',return_value={'budget':limits}), contextlib.redirect_stdout(output):
            runner_main()
        plan=json.loads(output.getvalue())
        self.assertEqual(plan['classification'],'SINGLE_VPS_CANDIDATE_PATH')
        self.assertEqual(plan['payload_max_bytes'],2*1024**2)
        self.assertEqual(plan['preflight']['budget']['runtime_max_seconds'],180)
        for value in ('--property=RuntimeMaxSec=180','--property=CPUQuota=60%',
                      '--build','bulk','--separate-dc','--candidate-check'):
            self.assertIn(value,plan['command'])
        self.assertNotIn('--benchmark',plan['command'])

    def test_explicit_soft_policy_keeps_all_hard_limits_and_reserves(self):
        standard = budget(956*MIB, 640*MIB, 4096*MIB)
        alternative = budget(956*MIB, 640*MIB, 4096*MIB, memory_high_percent=80)
        for key in standard:
            if key not in ('memory_high_bytes', 'memory_high_percent'):
                self.assertEqual(standard[key], alternative[key], key)
        self.assertEqual(standard['memory_high_percent'], 75)
        self.assertEqual(standard['memory_high_bytes'], standard['memory_max_bytes']*3//4)
        self.assertEqual(alternative['memory_high_bytes'], alternative['memory_max_bytes']*80//100)
        self.assertLess(alternative['memory_high_bytes'], alternative['memory_max_bytes'])
        for invalid in (0, 100, -1, True, 75.0):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                budget(956*MIB, 640*MIB, 4096*MIB, memory_high_percent=invalid)

    @unittest.skipUnless(os.name == 'posix', 'POSIX report permissions')
    def test_reports_are_private_with_shell_umask_and_never_overwrite_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'supervisor.json'
            previous = os.umask(0o022)
            try:
                write_private_report(path, {'cleanup_verified': True})
            finally:
                os.umask(previous)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text()), {'cleanup_verified': True})
            with self.assertRaises(FileExistsError):
                write_private_report(path, {'cleanup_verified': False})
            self.assertEqual(json.loads(path.read_text()), {'cleanup_verified': True})

    def test_escalation_rejects_actual_16_to_32_memory_high_shortfall(self):
        previous = {'exit': 0, 'stop_reason': None, 'cleanup_verified': True,
                    'host_oom_kill_delta': 0, 'samples': [{'memory.current': 188809216}]}
        result = escalation_budget(previous, {'eligible': True, 'memory_high_bytes': 169903104})
        self.assertFalse(result['eligible'])
        self.assertEqual(result['required_memory_high_bytes'], 207690138)
        self.assertTrue(escalation_budget(previous, {'eligible': True, 'memory_high_bytes': 208000000})['eligible'])

    def test_escalation_requires_measured_success_and_cleanup(self):
        previous = {'exit': 0, 'stop_reason': None, 'cleanup_verified': True,
                    'host_oom_kill_delta': 0, 'samples': [{'memory.current': 100}]}
        limits = {'eligible': True, 'memory_high_bytes': 110}
        for field, value in [('exit', 1), ('stop_reason', 'deadline'), ('cleanup_verified', False),
                             ('host_oom_kill_delta', 1), ('samples', []), ('samples', [{'memory.current': True}])]:
            with self.subTest(field=field, value=value):
                self.assertFalse(escalation_budget(dict(previous, **{field: value}), limits)['eligible'])
        self.assertFalse(escalation_budget(previous, {'eligible': False, 'memory_high_bytes': 1000})['eligible'])

    def test_failed_safety_queries_are_not_empty_successes(self):
        for failure in (FileNotFoundError(), subprocess.CalledProcessError(1, 'ip'),
                        subprocess.TimeoutExpired('ip', 5)):
            with self.subTest(failure=type(failure).__name__), patch('lab_preflight.subprocess.run', side_effect=failure):
                with self.assertRaisesRegex(RuntimeError, 'safety inspection failed'):
                    run_checked('ip', 'netns', 'list')
                remaining, errors = namespace_cleanup_status()
                self.assertIsNone(remaining)
                self.assertEqual(errors, ['namespace_inspection_failed'])
        with patch('lab_preflight.subprocess.run', return_value=Mock(stdout='')):
            self.assertEqual(namespace_cleanup_status(), ([], []))

    def test_small_vps_reserves_host_memory_and_disables_experiment_swap(self):
        limits = budget(956 * MIB, 616 * MIB, 4 * 1024 * MIB)
        self.assertTrue(limits["eligible"])
        self.assertLessEqual(limits["memory_max_bytes"], (616 * MIB - limits["host_reserve_bytes"]) * 3 // 4)
        self.assertEqual(limits["swap_max_bytes"], 0)
        self.assertEqual(limits["cpu_quota_percent"], 60)

    def test_low_memory_or_disk_refuses_instead_of_enlarging_limits(self):
        for total, available, disk in ((512, 500, 4096), (956, 400, 4096), (956, 700, 500), (956, 0, 4096)):
            with self.subTest(total=total, available=available, disk=disk):
                self.assertFalse(budget(total*MIB, available*MIB, disk*MIB)["eligible"])

    def test_systemd_owns_all_descendants_with_finite_resource_limits(self):
        limits = budget(956*MIB, 616*MIB, 4096*MIB)
        argv = systemd_command("tdh-audit-test", limits, ["python3", "synthetic-lab.py"], 600)
        for value in ("--wait", "--property=KillMode=control-group", "--property=CPUQuota=60%",
                      "--property=MemorySwapMax=0", "--property=RuntimeMaxSec=600", "--property=TasksMax=192",
                      "--property=OOMPolicy=stop", "--property=UMask=0077"):
            self.assertIn(value, argv)
        self.assertNotIn("--collect", argv)
        self.assertEqual(argv[-3:], ["--", "python3", "synthetic-lab.py"])

    def test_memory_accounting_keeps_socket_cache_and_anonymous_pages_separate(self):
        values={'memory.current':'100','memory.stat':'anon 30\nfile 40\nsock 20\n',
                'memory.pressure':'some avg10=1.00 avg60=0.00 avg300=0.00 total=1234'}
        with patch('lab_runner.read',side_effect=lambda p:values.get(p.name,'')):
            sample=cgroup_sample('/system.slice/tdh-audit-test.service')
        self.assertEqual(sample['memory.stat'],{'anon':30,'file':40,'sock':20})
        self.assertIn('total=1234',sample['memory.pressure'])

    @unittest.skipUnless(sys.platform == "linux", "namespace tool is Linux only")
    def test_ab_order_is_paired_and_reversed_each_round(self):
        spec = importlib.util.spec_from_file_location("upload_ab", Path(__file__).with_name("upload_ab.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.paired_order(0), ("flush", "auto"))
        self.assertEqual(module.paired_order(1), ("auto", "flush"))
        self.assertEqual(module.paired_order(2), ("flush", "auto"))
        self.assertEqual(module.policy_order(0),('pass','auto','client-auto','dc-auto'))
        self.assertEqual(module.policy_order(1),module.policy_order(0)[::-1])

    @unittest.skipUnless(sys.platform=='linux','Linux namespace harness')
    def test_stalled_worker_termination_still_returns_failure_for_retention(self):
        spec=importlib.util.spec_from_file_location('upload_ab_stop',Path(__file__).with_name('upload_ab.py'))
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        worker=Mock()
        worker.wait.side_effect=[subprocess.TimeoutExpired('worker',100),subprocess.TimeoutExpired('worker',10),0]
        self.assertEqual(module.wait_worker(worker),124)
        worker.terminate.assert_called_once()
        worker.kill.assert_called_once()
