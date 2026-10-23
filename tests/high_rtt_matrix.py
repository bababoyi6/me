"""Run a bounded native high-RTT A/B matrix, retaining expected baseline failures."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

from network_lab import Network,NAMES
parser=argparse.ArgumentParser()
parser.add_argument('--root',required=True)
parser.add_argument('--haproxy',default='haproxy')
parser.add_argument('--wg',default='wg')
parser.add_argument('--builds',nargs='+',choices=('official','baseline','flush','auto'))
parser.add_argument('--rtt',type=int,choices=(100,200),default=100)
parser.add_argument('--loss-percent',type=float,choices=(0,.1,.5),default=0)
parser.add_argument('--rate-mbps',type=int,choices=(100,1000),default=100)
parser.add_argument('--dc-timeout',type=int,choices=(8,25),default=8)
parser.add_argument('--rounds',type=int,choices=(1,3))
parser.add_argument('--observe-network',action='store_true')
profile=parser.add_mutually_exclusive_group()
profile.add_argument('--stress',action='store_true')
profile.add_argument('--long',action='store_true')
profile.add_argument('--backpressure',action='store_true')
args=parser.parse_args()
if args.dc_timeout!=8 and not args.backpressure:
    parser.error('extended mock DC deadline is only for bounded slow-reader control')
if args.rounds is not None and not args.backpressure:
    parser.error('explicit rounds are only for bounded slow-reader control')
if args.observe_network and not args.backpressure:
    parser.error('network observation is only for bounded slow-reader control')
if args.loss_percent and (args.stress or args.long or args.backpressure):
    parser.error('loss profile uses its own bounded 4 MiB payloads')
ROOT=Path(args.root).resolve()
HERE=Path(__file__).resolve().parent
signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
net=Network(SimpleNamespace(root=str(ROOT),wg=args.wg,start_iperf=False,
                            rtt_ms=args.rtt,loss_percent=args.loss_percent,rate_mbps=args.rate_mbps,mtu=1420))
(ROOT/"results").mkdir(exist_ok=True,mode=0o700)
temp=tempfile.TemporaryDirectory(prefix='tdh-audit-native-')
directory=Path(temp.name)
os.chown(directory,65534,65534)
server=None
workers=[]
try:
    net.setup()
    status=directory/'dc.json'
    server=subprocess.Popen(['ip','netns','exec',NAMES[2],'python3',str(HERE/'mock_dc_worker.py'),'--output',str(status),
                             '--socket-timeout',str(args.dc_timeout)],
                            stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    deadline=time.monotonic()+5
    while not status.exists():
        assert server.poll() is None,'DC startup failure'
        assert time.monotonic()<deadline,'DC readiness deadline'
        time.sleep(.1)
    builds=args.builds or (('flush','auto') if args.stress or args.long or args.backpressure else ('official','auto','baseline','flush'))
    for build in builds:
        loss_label=(f'timeout{args.dc_timeout}-' if args.dc_timeout!=8 else '')+(f'rate{args.rate_mbps}-' if args.rate_mbps!=100 else '')+(f'loss{args.loss_percent:g}-'.replace('.','p') if args.loss_percent else '')
        label=f'window-{args.rtt}-'+('observed-' if args.observe_network else '')+(f'rounds{args.rounds}-' if args.rounds else '')+loss_label+('stress-' if args.stress else 'long-' if args.long else 'backpressure-' if args.backpressure else '')+build
        output=ROOT/'results'/f'{label}.json'
        extra=['--streams','16','--bytes',str(128*1024**2),'--rounds','1','--small-probes'] if args.stress else ['--rounds','3']
        if args.long:
            extra=['--streams','1','--bytes',str(64*1024**2),'--rounds','3','--small-probes']
        if args.backpressure:
            extra=['--streams','32','--bytes',str(256*1024**2),'--rounds',str(args.rounds or 1),'--small-probes','--slow-readers']
        if args.loss_percent:
            extra=['--bytes',str(4*1024**2),'--rounds','3']
        worker=subprocess.Popen(['timeout','--signal=TERM','--kill-after=5','210','ip','netns','exec',NAMES[0],'python3',
                               str(HERE/'high_rtt_worker.py'),'--root',str(ROOT),'--haproxy',args.haproxy,'--build',build,'--output',str(output),
                               '--rate-mbps',str(args.rate_mbps),'--dc-timeout',str(args.dc_timeout),
                               *(['--observe-network'] if args.observe_network else []),*extra],
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
        workers.append(worker)
        stdout,stderr=worker.communicate(timeout=220)
        print(stdout,flush=True)
        if worker.returncode: raise RuntimeError('matrix worker failure: '+stderr[:3000])
    time.sleep(.2)
finally:
    for worker in workers:
        if worker.poll() is None:
            os.killpg(worker.pid,signal.SIGTERM)
            try: worker.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(worker.pid,signal.SIGKILL)
                worker.wait(timeout=2)
        if worker.stdout: worker.stdout.close()
        if worker.stderr: worker.stderr.close()
    if (directory/'dc.json').exists():
        loss_label=(f'timeout{args.dc_timeout}-' if args.dc_timeout!=8 else '')+(f'rate{args.rate_mbps}-' if args.rate_mbps!=100 else '')+(f'loss{args.loss_percent:g}-'.replace('.','p') if args.loss_percent else '')
        label=f"window-{args.rtt}-"+('observed-' if args.observe_network else '')+(f'rounds{args.rounds}-' if args.rounds else '')+f"{loss_label}{'stress-' if args.stress else 'long-' if args.long else 'backpressure-' if args.backpressure else ''}dc.json"
        (ROOT/'results'/label).write_text((directory/'dc.json').read_text())
    if server:
        server.terminate()
        try: server.wait(timeout=3)
        except subprocess.TimeoutExpired: server.kill();server.wait(timeout=2)
        server.stderr.close()
    net.close()
    temp.cleanup()
