"""Regression for real iproute2 empty class output on an unshaped WG link."""
import unittest
from unittest.mock import patch

from queue_probe import private_wg_snapshot


class QueueSnapshot(unittest.TestCase):
    def test_classless_empty_output_is_valid(self):
        with patch('queue_probe.command', side_effect=lambda *args: '' if 'class' in args else 'qdisc noqueue 0: root refcnt 2'):
            rows=private_wg_snapshot()
        self.assertTrue(all(row['class']=='' and row['qdisc'].startswith('qdisc noqueue ') for row in rows.values()))

    def test_missing_qdisc_must_not_be_silently_accepted(self):
        for value in ('', '[]', '{}'):
            with self.subTest(value=value),patch('queue_probe.command',return_value=value),self.assertRaises((ValueError,RuntimeError)):
                private_wg_snapshot()
