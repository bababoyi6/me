import copy
import unittest
from endurance_contract import schedule,validate,PAYLOAD_MAX


class EnduranceContractTests(unittest.TestCase):
    def fixture(self):
        plan=schedule(60)
        rows=[{'kind':'long','domain_index':i,'direction':d,'bytes':61440,
               'elapsed_seconds':60,'persistent_dc':True,'sha256_verified':True} for i,d in ((1,'D'),(2,'U'))]
        rows += [{'kind':'file','bytes':131072,'sha256_verified':True} for _ in range(4)]
        rows += [{'kind':'probe','bytes':1024,'sha256_verified':True}]
        return {'seconds_requested':60,'complete':True,'rows':rows,'backend_errors':[[],[]],
                'initial_route_delta':[1,1],'backend_identity_unchanged':True}

    def test_day_is_below_the_payload_cap(self):
        self.assertEqual(schedule(86400)['payload_bytes'],191004672)
        self.assertLess(schedule(86400)['payload_bytes'],PAYLOAD_MAX)
        with self.assertRaises(ValueError):schedule(3600)

    def test_interrupted_or_restarted_connections_cannot_pass(self):
        row=self.fixture();self.assertTrue(validate(row))
        for field,value in (('complete',False),('initial_route_delta',[2,0]),('backend_identity_unchanged',False),('backend_errors',[[{}],[]])):
            bad=copy.deepcopy(row);bad[field]=value
            with self.assertRaises(ValueError):validate(bad)
        for field,value in (('elapsed_seconds',59.9),('persistent_dc',False),('sha256_verified',False)):
            bad=copy.deepcopy(row);bad['rows'][0][field]=value
            with self.assertRaises(ValueError):validate(bad)

    def test_missing_probe_or_extra_payload_is_rejected(self):
        row=self.fixture();row['rows'].pop()
        with self.assertRaises(ValueError):validate(row)
        row=self.fixture();row['rows'][0]['bytes']+=1
        with self.assertRaises(ValueError):validate(row)
