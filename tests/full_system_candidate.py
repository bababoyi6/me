"""Pinned-candidate extensions to the maintained three-system KVM controller."""
import hashlib
import json
from pathlib import Path
import re
import shlex
import socket
import time

REPO=Path(__file__).resolve().parents[1]
OLD_ARCHIVE='b4fec3efd10288a9f211ad958337fa314a15486e72fd5c50ab870f22a19737e5'
OLD_BINARY='5dc5d9b5ac43a0f12a402159e127b3b69e18ccb355c99b0322e73d870d907100'


def expectations(ref,repo=REPO):
    if not re.fullmatch(r'[a-f0-9]{40}',ref):
        raise ValueError('full immutable candidate commit required')
    script=(repo/'telemt-dual-hop.sh').read_bytes()
    version=re.search(rb'^readonly TDH_VERSION="([0-9]+\.[0-9]+\.[0-9]+)"$',script,re.M).group(1).decode()
    lock=json.loads((repo/'docs/telemt-stable-lock.json').read_text())
    return {'manager':hashlib.sha256(script).hexdigest(),'core':lock['assets']['x86_64']['binary_sha256'],
            'manager_version':version,'core_version':'telemt '+lock['tag'],'notice_required':False,
            'source_ref':ref,'connect_policy_cli_required':b'connect-refresh --maintenance' in script}


def bootstrap(profile):
    if not re.fullmatch(r'[a-f0-9]{64}',profile['manager']):
        raise ValueError('candidate manager digest required')
    ref=profile['source_ref']
    if not re.fullmatch(r'[a-f0-9]{40}',ref):
        raise ValueError('immutable candidate source required')
    url=f'https://raw.githubusercontent.com/bababoyi6/me/{ref}/telemt-dual-hop.sh'
    # This cohort qualifies official-core migrations, not the independent
    # default-channel policy. Keep its starting identity explicit when the
    # production x86 default is balanced2.
    body=('set -e; export TDH_NEW_INSTALL_CORE=official; tmp=$(mktemp); trap \'rm -f -- "$tmp"\' EXIT; '
          'curl -q -fL --proto "=https" --proto-redir "=https" --tlsv1.2 --connect-timeout 10 '
          f'--max-time 120 --max-filesize 1048576 {shlex.quote(url)} -o "$tmp"; '
          f'printf "%s  %s\\n" {shlex.quote(profile["manager"])} "$tmp" | sha256sum --check --status; '
          'bash -n "$tmp"; bash "$tmp"')
    return 'bash -c '+shlex.quote(body)


# Only a digest of protected metadata crosses the guest boundary. Runtime caches,
# backup directories and logs are deliberately outside this configuration set.
PROTECTED="""python3 - <<'PY'
import hashlib,json,stat
from pathlib import Path
paths=[]
for name in ('/etc/telemt-dual-hop/keys','/etc/telemt-dual-hop/telemt','/etc/wireguard'):
 paths.extend(p for p in Path(name).rglob('*') if p.is_file() and not p.is_symlink())
paths.extend(p for p in Path('/etc/systemd/system').glob('*telemt-dual-hop*.service') if p.is_file())
paths.extend((Path('/etc/telemt-dual-hop/state.env'),Path('/usr/local/sbin/telemt-dual-hop'),Path('/usr/local/bin/a')))
rows={str(p):[hashlib.sha256(p.read_bytes()).hexdigest(),stat.S_IMODE(p.stat().st_mode),p.stat().st_uid,p.stat().st_gid] for p in paths}
assert rows
print(json.dumps({'sha256':hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest(),'files':len(rows)}))
PY
"""

STATS="""python3 - <<'PY'
import csv,io,json,socket
with socket.socket(socket.AF_UNIX) as s:
 s.settimeout(3);s.connect('/run/telemt-dual-hop/haproxy.sock');s.sendall(b'show stat\\n');data=b''
 while True:
  p=s.recv(8192)
  if not p:break
  data+=p
rows=csv.DictReader(io.StringIO(data.decode().lstrip('# ')))
print(json.dumps([{k:r[k] for k in ('pxname','svname','status','stot')} for r in rows if r.get('pxname') in ('telemt_a','telemt_b') and r.get('svname') in ('vps1','vps2')]))
PY
"""


def probe(guest,expected):
    from mtproto_transport import mtproxy_connect,req_pq
    credentials=json.loads(guest('entry',"""python3 - <<'PY'
import json,shlex
from pathlib import Path
d={}
for line in Path('/etc/telemt-dual-hop/state.env').read_text().splitlines():
 k,_,v=line.partition('=')
 if k in ('SECRET_A','SECRET_B'):d[k]=shlex.split(v)[0]
print(json.dumps(d))
PY
"""))
    before=json.loads(guest('entry',STATS))
    for domain,key in (('apple.com','SECRET_A'),('gs.apple.com','SECRET_B')):
        with socket.create_connection(('127.0.0.1',22443),timeout=15) as connection:
            connection.settimeout(15)
            assert req_pq(mtproxy_connect(connection,domain,bytes.fromhex(credentials[key]),fragment=True))
    after=json.loads(guest('entry',STATS))
    old={(r['pxname'],r['svname']):int(r['stot']) for r in before}
    for row in after:
        delta=int(row['stot'])-old[(row['pxname'],row['svname'])]
        if row['svname']==expected.get(row['pxname']):
            assert delta>=1,'expected backend did not carry the native probe'
        else:
            assert delta==0,'drained backend received a new session'
    return True


LEGACY_CONNECT_POLICY=r"""python3 - <<'PY'
from pathlib import Path
path=Path('/etc/telemt-dual-hop/telemt/telemt.toml')
text=path.read_text()
for old,new in (('tg_connect = 4\n','tg_connect = 10\n'),
                ('upstream_connect_budget_ms = 9000\n','upstream_connect_budget_ms = 3000\n')):
 assert text.count(old)==1,'managed connect fixture required'
 text=text.replace(old,new)
path.write_text(text)
PY
"""


def maintenance(guest,profile):
    result={}
    before=json.loads(guest('backend1',PROTECTED))
    guest('entry','a backend-drain 1\n')
    result['drain_routing_verified']=probe(guest,{'telemt_a':'vps2','telemt_b':'vps2'})
    # This is explicit laboratory preparation of an old core, not a hidden
    # production downgrade or substitution of the candidate download boundary.
    policy_seed=LEGACY_CONNECT_POLICY if profile.get('connect_policy_cli_required') else ''
    seed=f'''set -Eeuo pipefail
install -d -m 0700 /root/tdh-old-core-fixture
curl -q -fL --proto '=https' --proto-redir '=https' --tlsv1.2 --connect-timeout 10 --max-time 120 --max-filesize 67108864 https://github.com/bababoyi6/me/releases/download/telemt-core-3.5.5-tdh.balanced2/telemt-3.5.5-tdh.balanced2-x86_64-linux-gnu.tar.gz -o /root/tdh-old-core-fixture/core.tar.gz
printf '%s  %s\\n' {OLD_ARCHIVE} /root/tdh-old-core-fixture/core.tar.gz | sha256sum --check --status
tar -xzf /root/tdh-old-core-fixture/core.tar.gz -C /root/tdh-old-core-fixture telemt
printf '%s  %s\\n' {OLD_BINARY} /root/tdh-old-core-fixture/telemt | sha256sum --check --status
systemctl stop telemt-dual-hop-telemt.service
install -o root -g root -m 0755 /root/tdh-old-core-fixture/telemt /usr/local/lib/telemt-dual-hop/telemt
chmod 0710 /etc/telemt-dual-hop
{policy_seed}systemctl start telemt-dual-hop-telemt.service
'''
    guest('backend1',seed,180)
    legacy_protected=json.loads(guest('backend1',PROTECTED))
    result['old_core_seeded']=True
    def core_is(sha):
        guest('backend1',f"printf '%s  %s\\n' {sha} /usr/local/lib/telemt-dual-hop/telemt | sha256sum --check --status\n")
        return True
    def ready():
        deadline=time.monotonic()+75
        while True:
            try:
                guest('backend1','a diagnose >/root/candidate-diagnose.log 2>&1\n',35)
                return
            except RuntimeError:
                if time.monotonic()>=deadline:raise
                time.sleep(2)
    ready()
    guest('backend1','a core-upgrade --maintenance >/root/core-upgrade.log 2>&1\n',180)
    result['upgrade_verified']=core_is(profile['core'])
    guest('backend1',"test \"$(stat -c %a /etc/telemt-dual-hop)\" = 750\n")
    result['config_access_migration_verified']=True
    guest('backend1','a core-upgrade --maintenance >/root/core-repeat.log 2>&1\n',180)
    result['repeat_upgrade_verified']=core_is(profile['core'])
    def connect(action,expected):
        guest('backend1',f'a connect-{action} --maintenance >/root/connect-{action}.log 2>&1\n'
              f'grep -q "{expected}" /root/connect-{action}.log\n',90)
    if profile.get('connect_policy_cli_required'):
        connect('refresh','committed')
        assert json.loads(guest('backend1',PROTECTED))==before,'connect migration changed protected bytes'
        result['connect_refresh_verified']=True
        connect('refresh','unchanged')
        result['repeat_connect_refresh_verified']=True
        guest('backend1','if a core-rollback --maintenance >/root/connect-drift-guard.log 2>&1; then exit 1; fi\n'
              'grep -q "protected configuration changed" /root/connect-drift-guard.log\n',90)
        result['connect_drift_guard_verified']=core_is(profile['core'])
        connect('rollback','recovered')
        assert json.loads(guest('backend1',PROTECTED))==legacy_protected,'connect rollback did not restore exact fixture bytes'
        result['connect_rollback_verified']=True
        connect('rollback','unchanged')
        result['repeat_connect_rollback_verified']=True
    guest('backend1','a core-rollback --maintenance >/root/core-rollback.log 2>&1\n',120)
    result['rollback_verified']=core_is(OLD_BINARY)
    guest('backend1',"test \"$(stat -c %a /etc/telemt-dual-hop)\" = 710\n")
    result['config_access_rollback_verified']=True
    guest('backend1','a core-rollback --maintenance >/root/core-rollback-repeat.log 2>&1\n',120)
    result['repeat_rollback_verified']=core_is(OLD_BINARY)
    guest('backend1','a core-upgrade --maintenance >/root/core-final-upgrade.log 2>&1\n',180)
    result['final_core_verified']=core_is(profile['core'])
    if profile.get('connect_policy_cli_required'):
        connect('refresh','committed')
        result['connect_final_policy_verified']=True
    guest('backend1','a health-refresh --maintenance >/root/health-refresh.log 2>&1\n',90)
    result['health_refresh_verified']=True
    after=json.loads(guest('backend1',PROTECTED))
    assert after==before,'maintenance changed protected configuration or permissions'
    result['configuration_unchanged']=True
    ready()
    guest('entry','a backend-resume 1\n')
    # Agent and HAProxy convergence are sampled before asserting primary routing.
    deadline=time.monotonic()+65
    while True:
        rows=json.loads(guest('entry',STATS))
        if all(r['status']=='UP' for r in rows):break
        assert time.monotonic()<deadline,'resumed backend convergence deadline'
        time.sleep(2)
    result['resume_routing_verified']=probe(guest,{'telemt_a':'vps1','telemt_b':'vps2'})
    return result
