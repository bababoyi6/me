"""Real HTTP trickle fault against old/new agents in a disposable systemd netns."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import socket
import socketserver
import subprocess
import tempfile
import threading
import time

from stable_system_lab import agent_state, wait_for, systemctl


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-source',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    if (os.geteuid()!=0 or os.environ.get('GITHUB_ACTIONS')!='true' or
            os.readlink('/proc/self/ns/net')==os.readlink('/proc/1/ns/net')):
        raise RuntimeError('disposable CI private namespace required')
    fault=threading.Event();stop=threading.Event();entered=threading.Event()
    class Api(BaseHTTPRequestHandler):
        def do_GET(self):
            self.connection.settimeout(2)
            try:
                if fault.is_set():
                    self.send_response(200);self.send_header('Content-Length','16000');self.end_headers()
                    entered.set()
                    # Each read makes progress inside urllib's 2-second socket
                    # timeout, but the complete readiness result never arrives.
                    for _ in range(200):
                        if stop.is_set():break
                        self.wfile.write(b' ');self.wfile.flush();stop.wait(.2)
                else:
                    body=b'{"ok":true,"data":{"ready":true}}'
                    self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers()
                    self.wfile.write(body)
            except OSError:pass
        def log_message(self,*args):pass
    class Accept(socketserver.BaseRequestHandler):
        def handle(self):pass
    class Listener(socketserver.ThreadingTCPServer):
        daemon_threads=True
    api=ThreadingHTTPServer(('127.0.0.1',0),Api)
    dc=Listener(('127.0.0.1',0),Accept)
    for server in (api,dc):threading.Thread(target=server.serve_forever,daemon=True).start()
    report={'source_commit':os.environ['TDH_SOURCE_COMMIT'],'complete':False,'rows':[],
            'scope':'real HTTP body trickle / real old and new agent / actual systemd restart; synthetic local DC'}
    unit='tdh-audit-health-progress-'+str(os.getpid())+'.service'
    unit_path=Path('/run/systemd/system')/unit
    if unit_path.exists():raise RuntimeError('owned unit already exists')
    try:
        with tempfile.TemporaryDirectory(prefix='tdh-audit-health-progress-') as name:
            root=Path(name);root.chmod(0o755)
            token=root/'token';token.write_text('synthetic-local-token\n');token.chmod(0o640);os.chown(token,0,65534)
            for kind,source in (('baseline',Path(args.baseline_source)),
                                ('candidate',Path(__file__).resolve().parents[1]/'tools/health_agent.py')):
                fault.clear();entered.clear()
                staged=root/'agent.py';staged.write_bytes(source.read_bytes());staged.chmod(0o644)
                with socket.socket() as port:
                    port.bind(('127.0.0.1',0));health_port=port.getsockname()[1]
                unit_path.write_text(f'''[Unit]
Description=Disposable health progress fault
[Service]
Type=exec
User=nobody
Group=nogroup
ExecStart=/usr/bin/python3 {staged}
NetworkNamespacePath=/proc/{os.getpid()}/ns/net
Environment=TDH_BIND_IP=127.0.0.1 TDH_AGENT_PORT={health_port} TDH_API_PORT={api.server_port} TDH_TELEMT_PORT={dc.server_address[1]} TDH_DC_IPV4=127.0.0.1 TDH_DC_PORT={dc.server_address[1]} TDH_API_TOKEN_FILE={token}
Restart=on-failure
RestartSec=1
RuntimeMaxSec=140
CPUQuota=100%
MemoryMax=128M
MemorySwapMax=0
TasksMax=32
NoNewPrivileges=true
StandardOutput=null
StandardError=null
''')
                systemctl('daemon-reload');systemctl('start',unit)
                row={'kind':kind,'agent_sha256':hashlib.sha256(staged.read_bytes()).hexdigest()}
                report['rows'].append(row)
                row['cold_up_seconds']=wait_for(lambda:agent_state(health_port,b'up'),35)
                pid=int(systemctl('show',unit,'--property=MainPID','--value'))
                fault.set()
                if not entered.wait(4):raise RuntimeError('trickle fixture did not start')
                row['stale_admission_down_seconds']=wait_for(lambda:agent_state(health_port,b'down'),9)
                if kind=='baseline':
                    time.sleep(12)
                    row['stalled_live_monitor_unrecovered']=(int(systemctl('show',unit,'--property=MainPID','--value'))==pid
                        and agent_state(health_port,b'down'))
                    if not row['stalled_live_monitor_unrecovered']:raise RuntimeError('baseline did not reproduce')
                else:
                    row['restart_after_down_seconds']=wait_for(lambda:int(systemctl('show',unit,'--property=MainPID','--value')) not in (0,pid),20)
                    fault.clear()
                    row['recovered_up_seconds']=wait_for(lambda:agent_state(health_port,b'up'),35)
                    row['restart_count']=int(systemctl('show',unit,'--property=NRestarts','--value'))
                    if row['restart_count']!=1:raise RuntimeError('unexpected restart loop')
                systemctl('stop',unit)
            report['complete']=True
    finally:
        subprocess.run(['systemctl','stop',unit],capture_output=True,timeout=15)
        unit_path.unlink(missing_ok=True);systemctl('daemon-reload')
        subprocess.run(['systemctl','reset-failed',unit],capture_output=True,timeout=5)
        stop.set()
        for server in (api,dc):server.shutdown();server.server_close()
        report['temporary_unit_removed']=not unit_path.exists()
        Path(args.output).write_text(json.dumps(report,indent=2)+'\n')
    return 0 if report['complete'] and report['temporary_unit_removed'] else 1


if __name__=='__main__':
    signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
    raise SystemExit(main())
