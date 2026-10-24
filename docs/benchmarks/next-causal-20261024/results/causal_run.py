"""Reuse the checked-in bounded supervisor; save wrapper output separately."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

base = Path('/root/tdh-audit-20261021')
root = base/'tdh-audit-causal-20261023'
runtime=base/'runtime'
os.environ['PATH']=str(runtime/'usr/sbin')+':'+str(runtime/'usr/bin')+':'+os.environ['PATH']
os.environ['LD_LIBRARY_PATH']=str(runtime/'usr/lib/x86_64-linux-gnu')
argv=['python3',str(root/'project/tools/lab_runner.py'),'--root',str(root),
      '--haproxy',str(runtime/'usr/sbin/haproxy'),'--wg',str(runtime/'usr/bin/wg'),'--execute',*sys.argv[1:]]
log=root/'results'/('supervisor-'+str(time.time_ns())+'.log')
with log.open('x') as output:
    result=subprocess.run(argv,stdout=output,stderr=subprocess.STDOUT)
print(json.dumps({'exit':result.returncode,'log':log.name,'tail':log.read_text()[-3500:]}))
