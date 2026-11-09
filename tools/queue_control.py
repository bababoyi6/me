"""Opt-in backend WG egress shaping; no daemon, downloads or global networking.

The manager holds its ordinary lock for operator actions. WG PostStart/PostStop
uses a separate bounded lock, so restarting WG never deadlocks on the manager.
Failure of the optional hook must not fail the tunnel or Telemt dependency.
"""
import argparse
from contextlib import contextmanager
from decimal import Decimal
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile


class QueueError(RuntimeError):
    pass


def run(*args):
    try:
        return subprocess.run(args, check=True, capture_output=True, text=True, timeout=10).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise QueueError(f'{Path(args[0]).name}操作失败；已保留恢复记录') from exc


def safe_directory(path):
    path=Path(path)
    info=path.lstat()
    if path.resolve()!=path or not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o022:
        raise QueueError('目录权限或类型异常')


def read_file(path, private=True, limit=65536):
    safe_directory(path.parent)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1 or
            info.st_mode & (0o077 if private else 0o022) or info.st_size>limit):
            raise QueueError('文件权限或类型异常')
        return stream.read(limit+1)


def durable(path, data, mode=0o600):
    safe_directory(path.parent)
    exists=path.exists() or path.is_symlink()
    if exists:read_file(path,mode==0o600)
    fd,name=tempfile.mkstemp(prefix='.queue-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data);os.fchmod(stream.fileno(),mode);stream.flush();os.fsync(stream.fileno())
        if exists:os.replace(name,path)
        else:os.link(name,path)
        sync(path.parent)
    finally:Path(name).unlink(missing_ok=True)


def sync(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)


def encoded(value):return (json.dumps(value,sort_keys=True)+'\n').encode()
def digest(value):return hashlib.sha256(encoded(value)).hexdigest()


def policy(interface,rate,path_mbps):
    if interface not in ('tdh-b1','tdh-b2'):
        raise QueueError('只支持后端单隧道；入口共享带宽尚未开放，不能给两条隧道各分配整机带宽')
    if any(type(v) is not int for v in (rate,path_mbps)) or not 1<=rate<=900 or not rate<path_mbps<=1000 or rate*10>path_mbps*9:
        raise QueueError('请输入1–1000Mbps内的实测路径容量，发送预算须至少预留10%，最大900Mbps')
    return {'schema':1,'interface':interface,'rate_mbps':rate,'path_mbps':path_mbps}


class KernelQueue:
    ROOT='7a00:'
    LEAF='7a01:'

    def __init__(self,interface):self.interface=interface

    def link(self):
        links=json.loads(run('ip','-d','-j','link','show'))
        rows=[v for v in links if v['ifname']==self.interface]
        if not rows:return None
        row=rows[0]
        if row.get('linkinfo',{}).get('info_kind')!='wireguard':raise QueueError('目标不是WireGuard接口')
        return {'boot':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'netns':os.stat('/proc/self/ns/net').st_ino,'ifindex':row['ifindex'],'txqlen':row.get('txqlen',0)}

    def shape(self):
        # Do not request -j/-s together: Ubuntu22.04 HTB prints legacy xstats.
        qdisc=run('tc','-d','qdisc','show','dev',self.interface)
        classes=run('tc','-d','class','show','dev',self.interface)
        headers=[line.strip() for line in qdisc.splitlines() if line.startswith('qdisc ')]
        if not headers:raise QueueError('缺少队列观察，拒绝修改')
        htb=[line.strip() for line in classes.splitlines() if line.startswith('class htb ')]
        other=[line for line in classes.splitlines() if line.startswith('class ') and not line.startswith(('class htb ','class fq_codel '))]
        if other:raise QueueError('发现未知分类，拒绝修改')
        # Live fq_codel flow classes and direct-packet counters are statistics,
        # not configuration. Everything else remains part of the fingerprint.
        headers=[re.sub(r' direct_packets_stat \d+| refcnt \d+','',line) for line in headers]
        return {'qdisc':headers,'class':htb}

    def fresh(self):
        link=self.link()
        if link is None:return False
        shape=self.shape()
        return len(shape['qdisc'])==1 and shape['qdisc'][0].startswith('qdisc noqueue 0: root') and not shape['class']

    def owned(self,shape,rate,complete=True):
        kinds=[re.match(r'qdisc (\S+) (\S+) ',s).groups() for s in shape['qdisc']]
        expected=[('htb',self.ROOT),('fq_codel',self.LEAF)]
        if not kinds or any(v not in expected for v in kinds) or len(kinds)!=len(set(kinds)):
            raise QueueError('队列不属于本事务，拒绝覆盖或删除')
        if ('htb',self.ROOT) not in kinds or (complete and set(kinds)!=set(expected)):
            raise QueueError('队列结构不完整')
        if any(' root' not in s for s in shape['qdisc'] if s.startswith('qdisc htb ')):
            raise QueueError('根队列结构变化')
        if any(f' parent {self.ROOT}10 ' not in s+' ' for s in shape['qdisc'] if s.startswith('qdisc fq_codel ')):
            raise QueueError('叶队列结构变化')
        if len(shape['class'])>(1 if not complete else 1) or (complete and len(shape['class'])!=1):
            raise QueueError('HTB分类发生变化')
        for line in shape['class']:
            if not line.startswith(f'class htb {self.ROOT}10 root'):
                raise QueueError('HTB分类归属异常')
            for name in ('rate','ceil'):
                found=re.search(r'\b'+name+r' (\d+(?:\.\d+)?)([KMG]?)bit\b',line)
                if not found or Decimal(found[1])*{'':1,'K':1000,'M':1000000,'G':1000000000}[found[2]]!=rate*1000000:
                    raise QueueError('队列带宽发生漂移，拒绝覆盖')
        if run('tc','filter','show','dev',self.interface,'parent',self.ROOT):
            raise QueueError('发现自定义过滤器，拒绝覆盖')

    def create(self,rate):
        run('ip','link','set','dev',self.interface,'txqueuelen','1000')
        run('tc','qdisc','add','dev',self.interface,'root','handle',self.ROOT,'htb','default','10')
        run('tc','class','add','dev',self.interface,'parent',self.ROOT,'classid',self.ROOT+'10',
            'htb','rate',f'{rate}mbit','ceil',f'{rate}mbit','quantum','1514')
        run('tc','qdisc','add','dev',self.interface,'parent',self.ROOT+'10','handle',self.LEAF,'fq_codel')

    def restore(self,rate,baseline,expected=None):
        link=self.link()
        if link is None:return
        if expected is not None and link['txqlen']!=1000:raise QueueError('接口队列长度发生漂移')
        shape=self.shape()
        if len(shape['qdisc'])==1 and shape['qdisc'][0].startswith('qdisc noqueue 0: root') and not shape['class']:
            if expected is not None or link['txqlen'] not in (baseline,1000):raise QueueError('原队列已经变化，保留现场')
            run('ip','link','set','dev',self.interface,'txqueuelen',str(baseline))
            return
        self.owned(shape,rate,complete=expected is not None)
        if expected is not None and digest(shape)!=expected:raise QueueError('队列配置发生漂移，保留现场')
        run('tc','qdisc','del','dev',self.interface,'root')
        run('ip','link','set','dev',self.interface,'txqueuelen',str(baseline))
        if not self.fresh() or self.link()['txqlen']!=baseline:raise QueueError('队列恢复未完成，保留记录')


class QueueControl:
    def __init__(self,directory,interface,manager,dropin=None):
        self.directory=Path(directory);self.interface=interface;self.manager=Path(manager)
        self.dropin=Path(dropin or f'/etc/systemd/system/wg-quick@{interface}.service.d/90-telemt-queue.conf')
        self.config=self.directory/'policy.json';self.journal=self.directory/'runtime.json'
        self.stopping=self.directory/'stopping'
        self.kernel=KernelQueue(interface)

    @contextmanager
    def locked(self,create=False):
        safe_directory(self.directory.parent)
        if create:self.directory.mkdir(mode=0o700,exist_ok=True)
        safe_directory(self.directory)
        if self.directory.stat().st_mode & 0o077:raise QueueError('队列记录目录必须为0700')
        fd=os.open(self.directory/'lock',os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK|(os.O_CREAT if create else 0),0o600)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1 or info.st_mode & 0o077:
                raise QueueError('队列锁不安全')
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise QueueError('队列操作正在进行，请稍后重试') from None
            yield
        finally:os.close(fd)

    def desired(self):
        value=json.loads(read_file(self.config))
        if type(value) is not dict or type(value.get('schema')) is not int or set(value)!={'schema','interface','rate_mbps','path_mbps'} or value!=policy(self.interface,value.get('rate_mbps'),value.get('path_mbps')):
            raise QueueError('队列策略内容无效')
        return value

    def record(self,value):durable(self.journal,encoded(value))

    def prior(self,value):
        if not self.journal.exists() and not self.journal.is_symlink():return None
        record=json.loads(read_file(self.journal))
        if (type(record) is not dict or type(record.get('schema')) is not int or record.get('schema')!=1 or record.get('policy')!=digest(value) or
            type(record.get('baseline_txqlen')) is not int or not 0<=record['baseline_txqlen']<=10000 or record.get('phase') not in ('applying','active') or
            type(record.get('epoch')) is not dict):raise QueueError('恢复记录与策略不一致')
        epoch=record['epoch']
        if (set(epoch)!={'boot','netns','ifindex'} or not isinstance(epoch['boot'],str) or
            not re.fullmatch('[a-f0-9-]{36}',epoch['boot']) or
            any(type(epoch[k]) is not int or epoch[k]<=0 for k in ('netns','ifindex'))):raise QueueError('接口身份记录无效')
        if record['phase']=='active' and not re.fullmatch('[a-f0-9]{64}',record.get('fingerprint','')):
            raise QueueError('恢复记录缺少队列指纹')
        return record

    def apply(self,value):
        link=self.kernel.link()
        if link is None:raise QueueError('WireGuard接口未启动；未改变服务')
        epoch={k:link[k] for k in ('boot','netns','ifindex')}
        record=self.prior(value)
        if record and record['epoch']==epoch:
            shape=self.kernel.shape()
            if record['phase']=='active':
                self.kernel.owned(shape,value['rate_mbps'])
                if digest(shape)!=record['fingerprint'] or link['txqlen']!=1000:raise QueueError('队列配置发生漂移')
                return 'unchanged'
            self.kernel.restore(value['rate_mbps'],record['baseline_txqlen'])
        if not self.kernel.fresh():raise QueueError('接口已有自定义队列；拒绝接管')
        baseline=self.kernel.link()['txqlen']
        if type(baseline) is not int or not 0<=baseline<=10000:raise QueueError('原接口队列长度超出安全恢复范围')
        record={'schema':1,'policy':digest(value),'baseline_txqlen':baseline,'epoch':epoch,'phase':'applying'}
        self.record(record)
        try:
            self.kernel.create(value['rate_mbps'])
            shape=self.kernel.shape();self.kernel.owned(shape,value['rate_mbps'])
            record.update(phase='active',fingerprint=digest(shape));self.record(record)
        except BaseException:
            self.kernel.restore(value['rate_mbps'],baseline)
            self.journal.unlink(missing_ok=True);sync(self.directory)
            raise
        return 'applied'

    def remove(self,value):
        record=self.prior(value)
        link=self.kernel.link()
        if record and link is not None:
            epoch={k:link[k] for k in ('boot','netns','ifindex')}
            if record['epoch']==epoch:
                self.kernel.restore(value['rate_mbps'],record['baseline_txqlen'],record.get('fingerprint'))
            elif not self.kernel.fresh():raise QueueError('接口身份变化且有自定义队列，保留现场')
        elif link is not None and not self.kernel.fresh():
            raise QueueError('缺少归属记录，拒绝删除队列')
        self.journal.unlink(missing_ok=True);sync(self.directory)

    def hook(self):
        if not re.fullmatch(r'/[A-Za-z0-9_./-]+',str(self.manager)):raise QueueError('管理器路径不安全')
        return ('# Managed by Telemt Dual-Hop queue protocol 1\n[Service]\n'
            f'ExecStartPost=-/usr/bin/timeout --kill-after=2 12 {self.manager} --queue-service apply\n'
            f'ExecStopPost=-/usr/bin/timeout --kill-after=2 12 {self.manager} --queue-service remove\n').encode()

    def check_hook(self):
        if self.dropin.exists() or self.dropin.is_symlink():
            if read_file(self.dropin,False)!=self.hook():raise QueueError('队列启动配置发生漂移，拒绝覆盖')

    def enable(self,rate,path_mbps):
        value=policy(self.interface,rate,path_mbps)
        if b'# TDH_QUEUE_PROTOCOL=1\n' not in read_file(self.manager,False,1048576):raise QueueError('已安装管理器不支持队列控制，请先更新管理器')
        with self.locked(True):
            if self.stopping.exists() or self.stopping.is_symlink():raise QueueError('上次撤销未完成，请先运行queue disable --maintenance')
            self.check_hook()
            new=not self.config.exists() and not self.config.is_symlink()
            if self.config.exists() or self.config.is_symlink():
                if self.desired()!=value:raise QueueError('已有不同策略，请先撤销后重新启用')
            elif not self.kernel.fresh():raise QueueError('接口未就绪或已有自定义队列；未接管')
            if not self.dropin.parent.exists():
                safe_directory(self.dropin.parent.parent);self.dropin.parent.mkdir(mode=0o755)
            safe_directory(self.dropin.parent)
            try:
                durable(self.config,encoded(value));durable(self.dropin,self.hook(),0o644)
                run('systemctl','daemon-reload')
                return self.apply(value)
            except Exception:
                # Ordinary failures undo a fresh enable after kernel rollback.
                # SIGKILL leaves the durable intent/journal for safe resumption.
                if new and not self.journal.exists() and not self.journal.is_symlink():
                    self.check_hook()
                    if self.dropin.exists():self.dropin.unlink();sync(self.dropin.parent)
                    self.config.unlink(missing_ok=True);sync(self.directory)
                    run('systemctl','daemon-reload')
                raise

    def disable(self):
        if not self.directory.exists() and not self.directory.is_symlink():
            if self.dropin.exists() or self.dropin.is_symlink():raise QueueError('缺少策略归属信息，保留启动配置')
            return 'disabled'
        with self.locked(True):
            self.check_hook()
            durable(self.stopping,b'1\n')
            if self.config.exists() or self.config.is_symlink():self.remove(self.desired())
            elif self.journal.exists() or self.journal.is_symlink():raise QueueError('缺少策略，保留恢复记录')
            elif self.kernel.link() is not None and not self.kernel.fresh():raise QueueError('缺少策略归属，拒绝清理当前队列')
            if self.dropin.exists():self.dropin.unlink();sync(self.dropin.parent)
            run('systemctl','daemon-reload')
            self.config.unlink(missing_ok=True);sync(self.directory)
            self.stopping.unlink();sync(self.directory)
        return 'disabled'

    def service(self,action):
        if not self.config.exists() and not self.config.is_symlink():return 'disabled'
        with self.locked(True):
            self.check_hook();value=self.desired()
            if action=='apply' and (self.stopping.exists() or self.stopping.is_symlink()):
                if read_file(self.stopping)!=b'1\n':raise QueueError('撤销记录无效')
                return 'removal-pending'
            return self.apply(value) if action=='apply' else self.remove(value)

    def status(self):
        if not self.directory.exists() and not self.directory.is_symlink():return {'enabled':False}
        with self.locked():
            if not self.config.exists() and not self.config.is_symlink():
                if self.journal.exists() or self.dropin.exists():raise QueueError('缺少策略，需检查恢复记录')
                return {'enabled':False}
            value=self.desired();self.check_hook();record=self.prior(value);link=self.kernel.link()
            active=False
            if record and link and record['phase']=='active':
                active=(record['epoch']=={k:link[k] for k in ('boot','netns','ifindex')} and
                    link['txqlen']==1000 and digest(self.kernel.shape())==record['fingerprint'])
            return dict(value,enabled=True,active=active,boot_hook=self.dropin.exists(),removal_pending=self.stopping.exists())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base');parser.add_argument('interface');parser.add_argument('manager')
    parser.add_argument('action',choices=('enable','disable','status','apply','remove'))
    parser.add_argument('rate',nargs='?',type=int);parser.add_argument('path_mbps',nargs='?',type=int)
    parser.add_argument('--maintenance',action='store_true')
    parser.add_argument('--experimental',action='store_true')
    args=parser.parse_args();os.umask(0o077)
    if os.geteuid()!=0:raise QueueError('需要root权限')
    if args.action in ('enable','disable') and not args.maintenance:raise QueueError('请先排空本后端并添加--maintenance')
    if args.action=='enable' and not args.experimental:raise QueueError('尚未通过完整尾延迟门槛；只允许显式--experimental实验，不能默认用于生产')
    if args.action!='enable' and (args.rate is not None or args.path_mbps is not None):raise QueueError('参数数量无效')
    if args.interface not in ('tdh-b1','tdh-b2'):raise QueueError('只支持后端单隧道，入口共享整形尚未开放')
    control=QueueControl(Path(args.base)/'queue',args.interface,args.manager)
    result=(control.enable(args.rate,args.path_mbps) if args.action=='enable' else
            control.disable() if args.action=='disable' else control.status() if args.action=='status' else control.service(args.action))
    print(json.dumps({'queue':result},ensure_ascii=False))


if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('队列操作失败：'+(str(exc) if isinstance(exc,QueueError) else type(exc).__name__)+'；代理服务不会因此重启。',file=sys.stderr)
        raise SystemExit(1)
