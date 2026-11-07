"""A stopped process or zero exit is never sufficient full-system acceptance."""
ROLES={'entry','backend1','backend2'}
MANAGER='376f00d36a4fb1aa082002d96612d297d4f01e2e55935a1a3cf7155ed9f5d26f'
CORE='5dc5d9b5ac43a0f12a402159e127b3b69e18ccb355c99b0322e73d870d907100'


def validate(worker,supervisor,expected=None):
    # The historical published-1.6.0 contract remains the default. A candidate
    # supplies an independently pinned profile, never expectations from its report.
    profile=expected or {'manager':MANAGER,'core':CORE,'manager_version':'1.6.0',
                         'core_version':'telemt 3.5.5+tdh.balanced2','notice_required':True}
    if (worker.get('complete') is not True or supervisor.get('exit')!=0 or
            supervisor.get('stop_reason') is not None or supervisor.get('cleanup_verified') is not True or
            supervisor.get('host_oom_delta')!=0):
        raise ValueError('incomplete, interrupted or unsafe system test')
    roles=worker.get('roles',{})
    if set(roles)!=ROLES:
        raise ValueError('all three real roles required')
    for name,row in roles.items():
        if (row.get('manager_sha256')!=profile['manager'] or row.get('pid1')!='systemd' or
                row.get('installed_version')!=profile['manager_version'] or row.get('role')!=name or
                row.get('reboot_files_unchanged') is not True or row.get('new_boot_id') is not True):
            raise ValueError('role installation, identity or reboot preservation failed')
        if name!='entry' and (row.get('core_sha256')!=profile['core'] or row.get('core_version')!=profile['core_version'] or
                               (profile['notice_required'] and row.get('license_retained') is not True)):
            raise ValueError('installed backend core identity or license failed')
    if (worker.get('e2e_failover_verified') is not True or worker.get('post_reboot_sni_cases')!=2 or
            worker.get('download_boundary_substituted') is not False):
        raise ValueError('actual public install and service-path checks required')
    if expected is not None:
        if (worker.get('profile')!='candidate' or worker.get('source_ref')!=profile['source_ref'] or
                worker.get('pinned_manager_download') is not True):
            raise ValueError('candidate source identity must be pinned')
        repeats=worker.get('repeat_installs',{})
        if set(repeats)!=ROLES or any(row.get('files_unchanged') is not True for row in repeats.values()):
            raise ValueError('all three repeated installs must preserve protected files')
        maintenance=worker.get('native_maintenance',{})
        required=('drain_routing_verified','old_core_seeded','upgrade_verified','repeat_upgrade_verified',
                  'rollback_verified','repeat_rollback_verified','final_core_verified',
                  'health_refresh_verified','configuration_unchanged','resume_routing_verified',
                  'config_access_migration_verified','config_access_rollback_verified')
        if any(maintenance.get(key) is not True for key in required):
            raise ValueError('native maintenance CLI path incomplete')
    return True
