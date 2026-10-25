"""A stopped process or zero exit is never sufficient full-system acceptance."""
ROLES={'entry','backend1','backend2'}
MANAGER='376f00d36a4fb1aa082002d96612d297d4f01e2e55935a1a3cf7155ed9f5d26f'
CORE='5dc5d9b5ac43a0f12a402159e127b3b69e18ccb355c99b0322e73d870d907100'


def validate(worker,supervisor):
    if (worker.get('complete') is not True or supervisor.get('exit')!=0 or
            supervisor.get('stop_reason') is not None or supervisor.get('cleanup_verified') is not True or
            supervisor.get('host_oom_delta')!=0):
        raise ValueError('incomplete, interrupted or unsafe system test')
    roles=worker.get('roles',{})
    if set(roles)!=ROLES:
        raise ValueError('all three real roles required')
    for name,row in roles.items():
        if (row.get('manager_sha256')!=MANAGER or row.get('pid1')!='systemd' or
                row.get('installed_version')!='1.6.0' or row.get('role')!=name or
                row.get('reboot_files_unchanged') is not True or row.get('new_boot_id') is not True):
            raise ValueError('role installation, identity or reboot preservation failed')
        if name!='entry' and (row.get('core_sha256')!=CORE or row.get('core_version')!='telemt 3.5.5+tdh.balanced2' or
                               row.get('license_retained') is not True):
            raise ValueError('installed backend core identity or license failed')
    if (worker.get('e2e_failover_verified') is not True or worker.get('post_reboot_sni_cases')!=2 or
            worker.get('download_boundary_substituted') is not False):
        raise ValueError('actual public install and service-path checks required')
    return True
