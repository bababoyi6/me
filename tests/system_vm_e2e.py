"""Private full-system MTProto/failover functional checks; no user account."""
import csv,io,json,os,socket,sys,time
from system_vm_ssh import ROOT,ssh

from mtproto_transport import mtproxy_connect,req_pq
os.umask(0o077)
result={'scope':'three Ubuntu 22.04 KVM guests, real Telegram public DH only; no file speed/capacity claim','complete':False,'stages':[]}
output=ROOT/'results/e2e.json'
def save(): output.write_text(json.dumps(result,indent=2)+'\n')
def guest(node,code,seconds=30):
 r=ssh(node,code,seconds)
 if r.returncode:
  (ROOT/'private/e2e-error.log').write_text(r.stdout+r.stderr)
  raise RuntimeError('guest operation failed, private log retained')
 return r.stdout
credentials=json.loads(guest('entry',"python3 - <<'PY'\nimport json,shlex\nfrom pathlib import Path\nd={}\nfor line in Path('/etc/telemt-dual-hop/state.env').read_text().splitlines():\n k,_,v=line.partition('=')\n if k in ('SECRET_A','SECRET_B'):d[k]=shlex.split(v)[0]\nprint(json.dumps(d))\nPY\n"))
stats_script="""python3 - <<'PY'
import csv,io,json,socket
with socket.socket(socket.AF_UNIX) as s:
 s.settimeout(3);s.connect('/run/telemt-dual-hop/haproxy.sock');s.sendall(b'show stat\\n');data=b''
 while True:
  p=s.recv(8192)
  if not p:break
  data+=p
rows=csv.DictReader(io.StringIO(data.decode().lstrip('# ')))
print(json.dumps([{k:r[k] for k in ('pxname','svname','status','stot','scur')} for r in rows if r.get('pxname') in ('telemt_a','telemt_b') and r.get('svname') in ('vps1','vps2')]))
PY
"""
def stats(): return json.loads(guest('entry',stats_script))
def wait_state(server,up):
 deadline=time.monotonic()+65
 while time.monotonic()<deadline:
  rows=stats()
  selected=[r for r in rows if r['svname']==server]
  if len(selected)==2 and all(r['status'].startswith('UP')==up for r in selected):return rows
  time.sleep(2)
 raise RuntimeError('HAProxy real health convergence deadline')
def probe(label,fragment=False):
 before=stats(); cases=[]
 for domain,key in (('apple.com','SECRET_A'),('gs.apple.com','SECRET_B')):
  with socket.create_connection(('127.0.0.1',22443),timeout=15) as sock:
   sock.settimeout(15)
   stream=mtproxy_connect(sock,domain,bytes.fromhex(credentials[key]),dc=2,fragment=fragment)
   assert req_pq(stream)
   cases.append({'sni':domain,'authenticated_faketls':True,'res_pq_nonce_verified':True,'fragmented_hello':fragment})
 after=stats(); old={(r['pxname'],r['svname']):int(r['stot']) for r in before}
 routing=[{'backend':r['pxname'],'server':r['svname'],'session_delta':int(r['stot'])-old[(r['pxname'],r['svname'])]} for r in after]
 result['stages'].append({'stage':label,'cases':cases,'routing':routing,'health':after});save()
save()
try:
 wait_state('vps1',True);wait_state('vps2',True)
 probe('both_backends_healthy',True)
 guest('backend1','systemctl stop telemt-dual-hop-telemt.service\n')
 wait_state('vps1',False)
 probe('actual_backend1_telemt_stopped')
finally:
 guest('backend1','systemctl start telemt-dual-hop-telemt.service\n')
wait_state('vps1',True);probe('backend1_recovered')
result.update(complete=True,actual_backend_stop=True,existing_tcp_session_migration_tested=False)
save();print(json.dumps(result))
