"""Reject the observed 6s/16s false-low capacity result, never substitute sender."""
import copy
import unittest

from path_capacity import validate


def measured():
    return {'start': {'test_start': {'reverse': 1}},
        'end': {'sum_sent': {'seconds': 6.1, 'bytes': 138000000, 'bits_per_second': 138000000*8/6.1},
                'sum_received': {'seconds': 6, 'bytes': 137000000, 'bits_per_second': 137000000*8/6}},
        'intervals': [{'sum': {'omitted': False, 'sender': False, 'bytes': 137000000/6}} for _ in range(6)]}


class CapacityValidation(unittest.TestCase):
    def test_receiver_local_complete(self):
        self.assertAlmostEqual(validate(measured()), 182.6666666667)

    def test_actual_stalled_receiver_rollup_rejected(self):
        payload = measured()
        payload['end']['sum_received'] = {'start': 0, 'end': 16.121251, 'seconds': 16.121251,
            'bytes': 139926840, 'bits_per_second': 69437211.78958134}
        with self.assertRaisesRegex(ValueError, 'window mismatch'):
            validate(payload)

    def test_no_remote_or_missing_interval_fallback(self):
        for change in ('remote', 'intervals', 'bytes', 'rate', 'nonfinite'):
            with self.subTest(change=change):
                payload = copy.deepcopy(measured())
                if change == 'remote': payload['start']['test_start']['reverse'] = 0
                if change == 'intervals': payload['intervals'] = []
                if change == 'bytes': payload['end']['sum_sent']['bytes'] *= 2
                if change == 'rate': payload['end']['sum_received']['bits_per_second'] *= 2
                if change == 'nonfinite': payload['end']['sum_received']['seconds'] = float('nan')
                with self.assertRaises(ValueError): validate(payload)
