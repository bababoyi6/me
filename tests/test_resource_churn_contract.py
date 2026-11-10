import copy
import unittest
from resource_churn_lab import assess


class ResourceChurnContractTests(unittest.TestCase):
    def fixture(self):
        return {'elapsed_seconds':182,'samples':[{'rss_bytes':32*1024**2} for _ in range(30)],'errors':[],
                'config_unchanged':True,'all_payloads_verified':True,'private_processes_closed':True,
                'mock_dc_errors':[],'cgroup_before':{'memory_events':{'oom':0,'oom_kill':0}},
                'cgroup_after':{'memory_events':{'oom':0,'oom_kill':0}},'fd_before':16,'fd_after_idle':16,
                'rss_growth_after_warmup':4*1024**2}

    def test_partial_or_oom_or_unclosed_processes_cannot_pass(self):
        row=self.fixture();self.assertTrue(assess(row))
        for key,value in (('elapsed_seconds',179),('samples',[]),('private_processes_closed',False),
                          ('all_payloads_verified',False),('fd_after_idle',40),('rss_growth_after_warmup',32*1024**2)):
            bad=copy.deepcopy(row);bad[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):assess(bad)
        bad=copy.deepcopy(row);bad['cgroup_after']['memory_events']['oom_kill']=1
        with self.assertRaises(ValueError):assess(bad)
