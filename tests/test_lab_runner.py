"""Resource plans and executable arguments must fail closed before privileged setup."""
import importlib.util
from pathlib import Path
import sys
import unittest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
from lab_preflight import budget, MIB
from lab_runner import systemd_command
sys.path.remove(str(TOOLS))


class LabRunnerTests(unittest.TestCase):
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

    @unittest.skipUnless(sys.platform == "linux", "namespace tool is Linux only")
    def test_ab_order_is_paired_and_reversed_each_round(self):
        spec = importlib.util.spec_from_file_location("upload_ab", Path(__file__).with_name("upload_ab.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.paired_order(0), ("flush", "auto"))
        self.assertEqual(module.paired_order(1), ("auto", "flush"))
        self.assertEqual(module.paired_order(2), ("flush", "auto"))
