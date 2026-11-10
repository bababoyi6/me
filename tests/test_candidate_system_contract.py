import copy
from pathlib import Path
import shlex
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parent))
from full_system_candidate import expectations,bootstrap
from full_system_contract import validate,ROLES


class CandidateSystemContractTests(unittest.TestCase):
    def fixture(self):
        profile=expectations('a'*40)
        roles={name:{'manager_sha256':profile['manager'],'core_sha256':profile['core'],
                    'core_version':profile['core_version'],'installed_version':profile['manager_version'],
                    'pid1':'systemd','role':name,'reboot_files_unchanged':True,'new_boot_id':True}
               for name in ROLES}
        flags=('drain_routing_verified','old_core_seeded','upgrade_verified','repeat_upgrade_verified',
               'rollback_verified','repeat_rollback_verified','final_core_verified','health_refresh_verified',
               'configuration_unchanged','resume_routing_verified','config_access_migration_verified',
               'config_access_rollback_verified')
        flags+=('connect_refresh_verified','repeat_connect_refresh_verified',
                'connect_rollback_verified','repeat_connect_rollback_verified',
                'connect_drift_guard_verified','connect_final_policy_verified')
        worker={'complete':True,'roles':roles,'e2e_failover_verified':True,'post_reboot_sni_cases':2,
                'download_boundary_substituted':False,'profile':'candidate','source_ref':'a'*40,
                'pinned_manager_download':True,'repeat_installs':{r:{'files_unchanged':True} for r in ROLES},
                'native_maintenance':{k:True for k in flags}}
        supervisor={'exit':0,'stop_reason':None,'cleanup_verified':True,'host_oom_delta':0}
        return profile,worker,supervisor

    def test_candidate_requires_all_roles_reinstalls_and_cli_stages(self):
        profile,worker,supervisor=self.fixture()
        self.assertTrue(validate(worker,supervisor,profile))
        for name in worker['native_maintenance']:
            bad=copy.deepcopy(worker);bad['native_maintenance'][name]=False
            with self.subTest(name=name),self.assertRaises(ValueError):validate(bad,supervisor,profile)
        bad=copy.deepcopy(worker);del bad['repeat_installs']['backend2']
        with self.assertRaises(ValueError):validate(bad,supervisor,profile)
        legacy_profile=dict(profile);legacy_profile.pop('connect_policy_cli_required')
        legacy=copy.deepcopy(worker)
        for key in tuple(legacy['native_maintenance']):
            if 'connect_' in key:del legacy['native_maintenance'][key]
        self.assertTrue(validate(legacy,supervisor,legacy_profile))

    def test_unpinned_source_and_changed_reboot_files_are_rejected(self):
        profile,worker,supervisor=self.fixture()
        for key,value in (('source_ref','main'),('pinned_manager_download',False),('download_boundary_substituted',True)):
            bad=copy.deepcopy(worker);bad[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):validate(bad,supervisor,profile)
        bad=copy.deepcopy(worker);bad['roles']['backend1']['reboot_files_unchanged']=False
        with self.assertRaises(ValueError):validate(bad,supervisor,profile)

    def test_bootstrap_pins_ref_digest_and_https_without_main(self):
        profile,_,_=self.fixture()
        line=bootstrap(profile)
        argv=shlex.split(line)
        self.assertEqual(argv[:2],['bash','-c'])
        body=argv[2]
        self.assertIn('/'+'a'*40+'/telemt-dual-hop.sh',body)
        self.assertIn(profile['manager'],body)
        self.assertIn('sha256sum --check --status',body)
        self.assertIn('export TDH_NEW_INSTALL_CORE=official;',body)
        self.assertIn('--proto-redir "=https"',body)
        self.assertNotIn('/main/',body)
        for ref in ('main','a'*39,'a'*40+';touch /tmp/unsafe'):
            with self.assertRaises(ValueError):expectations(ref)


if __name__=='__main__':unittest.main()
