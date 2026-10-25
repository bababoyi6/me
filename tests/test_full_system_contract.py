import copy
import unittest
from full_system_contract import validate,MANAGER,CORE,ROLES


class FullSystemAcceptanceTests(unittest.TestCase):
    def fixture(self):
        rows={name:{'manager_sha256':MANAGER,'pid1':'systemd','installed_version':'1.6.0','role':name,
                    'reboot_files_unchanged':True,'new_boot_id':True,'core_sha256':CORE,
                    'core_version':'telemt 3.5.5+tdh.balanced2','license_retained':True} for name in ROLES}
        return {'complete':True,'roles':rows,'e2e_failover_verified':True,'post_reboot_sni_cases':2,
                'download_boundary_substituted':False},{'exit':0,'stop_reason':None,'cleanup_verified':True,'host_oom_delta':0}

    def test_three_real_roles_and_reboots_are_required(self):
        worker,supervisor=self.fixture();self.assertTrue(validate(worker,supervisor))
        for field,value in (('roles',{}),('e2e_failover_verified',False),('download_boundary_substituted',True)):
            bad=copy.deepcopy(worker);bad[field]=value
            with self.assertRaises(ValueError):validate(bad,supervisor)

    def test_zero_exit_with_interrupted_or_incomplete_worker_is_rejected(self):
        worker,supervisor=self.fixture()
        for key,value in (('complete',False),('post_reboot_sni_cases',0)):
            bad=copy.deepcopy(worker);bad[key]=value
            with self.assertRaises(ValueError):validate(bad,supervisor)
        for key,value in (('cleanup_verified',False),('host_oom_delta',1),('stop_reason','operator_interrupt')):
            bad=dict(supervisor,**{key:value})
            with self.assertRaises(ValueError):validate(worker,bad)

    def test_old_core_or_changed_reboot_state_cannot_pass(self):
        worker,supervisor=self.fixture()
        for key,value in (('core_sha256','0'*64),('reboot_files_unchanged',False),('new_boot_id',False)):
            bad=copy.deepcopy(worker);bad['roles']['backend1'][key]=value
            with self.assertRaises(ValueError):validate(bad,supervisor)
