"""Read-only inventory of existing disposable full-system fixtures."""
import importlib.util
import json
import os
from pathlib import Path
import shutil

BASE=Path('/root/tdh-audit-20261021')
ROOT=BASE/'vm-lab'
spec=importlib.util.spec_from_file_location('current_preflight',BASE/'vm160_preflight_module.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
report=module.inventory()
assert os.geteuid()==0 and ROOT.resolve()==ROOT and not ROOT.is_symlink()
assert ROOT.stat().st_uid==0 and ROOT.stat().st_mode & 0o077==0
report['scope']='READ_ONLY_CURRENT_HOST_AND_OWNED_VM_FIXTURES'
report['vm_disk_free_bytes']=shutil.disk_usage(ROOT).free
report['kvm_available']=Path('/dev/kvm').exists()
report['fixtures']={}
for name in ('entry','backend1','backend2'):
 folder=ROOT/name
 disk=folder/'disk.qcow2'
 item={'disk_exists':disk.is_file(),'disk_is_symlink':disk.is_symlink(),
       'logical_disk_file_bytes':disk.stat().st_size if disk.is_file() else None,
       'generic_boot_metadata_present':(folder/'boot-generic.json').is_file()}
 report['fixtures'][name]=item
report['owned_vm_processes']=[]
for p in Path('/proc').iterdir():
 if not p.name.isdigit():continue
 try:
  command=(p/'cmdline').read_bytes().split(b'\x00')
  if any(str(ROOT/name/'disk.qcow2').encode() in command for name in ('entry','backend1','backend2')):
   report['owned_vm_processes'].append(int(p.name))
 except OSError:pass
cg=Path('/sys/fs/cgroup/tdh-audit-vms')
report['vm_cgroup_exists']=cg.exists()
report['vm_cgroup']={n:(cg/n).read_text().strip() for n in ('memory.max','memory.high','memory.current','memory.events','cgroup.procs','cpu.max') if (cg/n).exists()}
report['owned_vm_key_mode']=oct((ROOT/'key').stat().st_mode & 0o777) if (ROOT/'key').is_file() else None
report['baseline_cores_present']={n:(BASE/n).is_file() for n in ('telemt-auto','telemt-flush')}
os.umask(0o077)
with (BASE/'vm160-preflight.json').open('x') as output:output.write(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
