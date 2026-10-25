import json,os,shutil
from pathlib import Path
p=Path('/root/tdh-audit-20261021/tdh-audit-vm160/results')
print(json.dumps({'available_kib':next(int(x.split()[1]) for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')),'disk_free':shutil.disk_usage(p).free,'reports':[q.name for q in p.glob('*.json')]}))
for cg in Path('/sys/fs/cgroup/system.slice').glob('tdh-audit-vm160-*.service'):
 print(json.dumps({'group':cg.name,**{n:(cg/n).read_text().strip() for n in ('memory.current','memory.events','memory.pressure','cpu.stat','io.stat') if (cg/n).exists()}}))

print(json.dumps({'private_transcript_status':[{'stage':q.name,'bytes':q.stat().st_size} for q in (p.parent/'private').glob('*.log')]}))
