"""Private lab telemetry and reversible WG qdisc counterfactuals; never host NICs."""
import json
import os
import re
import subprocess
import threading
import time

from network_lab import NAMES, command


def ping_value(text):
    match=re.search(r'time=([0-9]+(?:\.[0-9]+)?) ms',text)
    return float(match[1]) if match else None


class LatencyProbe:
    def __init__(self):
        if os.geteuid()!=0 or os.environ.get('GITHUB_ACTIONS')!='true':raise RuntimeError('CI-only probe')
        self.event=threading.Event();self.samples=[];self.failures=[];self.started=time.monotonic()
        self.thread=threading.Thread(target=self.run,name='private-path-probe')
        self.thread.start()
    def run(self):
        while not self.event.is_set():
            offset=time.monotonic()-self.started
            try:
                result=subprocess.run(['ip','netns','exec',NAMES[0],'ping','-n','-c','1','-W','2','10.77.1.2'],
                    capture_output=True,text=True,timeout=3,env=dict(os.environ,LC_ALL='C'))
                value=ping_value(result.stdout) if len(result.stdout)<=4096 else None
                if result.returncode or value is None:self.failures.append({'offset_seconds':offset,'type':'missing_reply'})
                else:self.samples.append({'offset_seconds':offset,'rtt_ms':value})
            except Exception as error:self.failures.append({'offset_seconds':offset,'type':type(error).__name__})
            self.event.wait(.1)
    def stop(self):
        self.event.set();self.thread.join(timeout=4)
        if self.thread.is_alive():raise RuntimeError('bounded latency probe failed to exit')
        return {'samples':self.samples,'failures':self.failures}


def original_wg_policy():
    result={}
    for namespace in (NAMES[0],NAMES[2]):
        link=json.loads(command('ip','-n',namespace,'-j','link','show','dev','wg-lab'))[0]
        qdisc=json.loads(command('ip','netns','exec',namespace,'tc','-j','qdisc','show','dev','wg-lab'))
        kinds=[r['kind'] for r in qdisc if r.get('root')]
        if len(kinds)!=1 or kinds[0] not in ('noqueue','fq'):raise RuntimeError('unexpected fresh WG qdisc')
        result[namespace]={'txqlen':link.get('txqlen',0),'kind':kinds[0]}
    return result


def configure_wg_fq(enabled,original):
    for namespace in (NAMES[0],NAMES[2]):
        before=original[namespace]
        if enabled or before['kind']=='fq':
            command('ip','-n',namespace,'link','set','dev','wg-lab','txqueuelen',str(1000 if enabled else before['txqlen']))
            command('ip','netns','exec',namespace,'tc','qdisc','replace','dev','wg-lab','root','fq')
        else:
            current=json.loads(command('ip','netns','exec',namespace,'tc','-j','qdisc','show','dev','wg-lab'))
            command('ip','-n',namespace,'link','set','dev','wg-lab','txqueuelen',str(before['txqlen']))
            if any(q.get('root') and q['kind']=='fq' for q in current):
                command('ip','netns','exec',namespace,'tc','qdisc','del','dev','wg-lab','root')
