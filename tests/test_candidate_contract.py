import copy
import unittest

from candidate_contract import VERSION,validate,validate_wireguard


class CandidatePathContractTests(unittest.TestCase):
    def test_both_tunnels_need_bidirectional_payload_sized_counters(self):
        rows,_=self.fixture()
        counters={name:{'rx':1024**2,'tx':1024**2} for name in ('wg-lab','wg-lab2')}
        validate_wireguard(rows,counters)
        for changed in ({'rx':0,'tx':1024**2},{'rx':1,'tx':1}):
            bad=copy.deepcopy(counters);bad['wg-lab2']=changed
            with self.assertRaises(ValueError):
                validate_wireguard(rows,bad)

    def fixture(self):
        rows=[{'domain_index':d,'direction':direction,'backend_index':b,'bytes':262144,'sha256_verified':True}
              for d,b in ((1,1),(2,2)) for direction in ('D','U')]
        rows += [{'domain_index':1,'direction':direction,'backend_index':b,'bytes':4096,'sha256_verified':True}
                 for direction,b in (('D',2),('U',2),('U',1))]
        states=[{'telemt':VERSION,'errors':[],'socket_observation_errors':[],
                 'client_sockets':[{'rtt_ms':100.5,'rb':131072}]} for _ in (1,2)]
        return rows,states

    def test_complete_protocol_route_and_measured_high_rtt(self):
        rows,states=self.fixture()
        self.assertEqual(len(validate(rows,states)),2)

    def test_low_or_unknown_rtt_and_wrong_candidate_do_not_pass(self):
        rows,states=self.fixture()
        for mutation in ({'client_sockets':[]},{'client_sockets':[{'rtt_ms':1}]},{'telemt':'telemt 3.5.5+tdh.audit2'},
                         {'errors':['timeout']},{'socket_observation_errors':['netlink']}):
            altered=copy.deepcopy(states);altered[1].update(mutation)
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                validate(rows,altered)

    def test_incomplete_or_wrong_route_or_oversized_payload_is_refused(self):
        rows,states=self.fixture()
        for key,value in (('backend_index',2),('sha256_verified',False),('bytes',3*1024**2)):
            altered=copy.deepcopy(rows);altered[0][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):
                validate(altered,states)
        with self.assertRaises(ValueError):
            validate(rows[:-1],states)
