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


def private_wg_snapshot():
    result={}
    for namespace in (NAMES[0],NAMES[2]):
        result[namespace]={}
        for kind in ('qdisc','class'):
            raw=command('ip','netns','exec',namespace,'tc','-s','-d',kind,'show','dev','wg-lab')
            # iproute2 5.15 HTB xstats uses fprintf even under -j, producing
            # invalid JSON. Preserve supported plain output, including counters.
            # Empty classes on noqueue are valid; absent qdisc evidence is not.
            if (kind=='qdisc' and not raw.startswith('qdisc ')) or (kind=='class' and raw and not raw.startswith('class ')):
                raise RuntimeError('private queue observation incomplete')
            result[namespace][kind]=raw
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


def configure_private_shaper(enabled, original):
    """One preregistered CI counterfactual: 180Mbps before 200Mbps encapsulation.

    Only fresh, owned private WG links; never a production tuning command.
    Keep flow visibility before encryption instead of shortening netem delay.
    """
    if os.geteuid()!=0 or os.environ.get('GITHUB_ACTIONS')!='true':
        raise RuntimeError('owned CI network required')
    for namespace in (NAMES[0],NAMES[2]):
        if original[namespace]['kind']!='noqueue':
            raise RuntimeError('shaper study requires fresh noqueue WireGuard links')
        current=json.loads(command('ip','netns','exec',namespace,'tc','-j','qdisc','show','dev','wg-lab'))
        roots=[q for q in current if q.get('root')]
        if len(roots)!=1 or roots[0]['kind'] not in ('noqueue','htb'):
            raise RuntimeError('refusing to replace unknown private queue')
        if roots[0]['kind']=='htb':
            command('ip','netns','exec',namespace,'tc','qdisc','del','dev','wg-lab','root')
        if enabled:
            command('ip','-n',namespace,'link','set','dev','wg-lab','txqueuelen','1000')
            command('ip','netns','exec',namespace,'tc','qdisc','add','dev','wg-lab','root','handle','1:','htb','default','10')
            command('ip','netns','exec',namespace,'tc','class','add','dev','wg-lab','parent','1:','classid','1:10',
                'htb','rate','180mbit','ceil','180mbit','quantum','1514')
            command('ip','netns','exec',namespace,'tc','qdisc','add','dev','wg-lab','parent','1:10','handle','10:','fq_codel')
        else:
            command('ip','-n',namespace,'link','set','dev','wg-lab','txqueuelen',str(original[namespace]['txqlen']))
