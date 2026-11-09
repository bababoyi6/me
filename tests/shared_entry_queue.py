"""CI-only shared cleartext WG scheduler; never touches a host/public NIC.

Two WG egress classifiers feed one IFB HTB/fq_codel budget, then Linux IFB
reinjects each packet into its original WG. No route, key or root qdisc changes.
This is a screening prototype, not a persistent production controller.
"""
import hashlib
import json
import os

from network_lab import NAMES, command


class SharedEntryQueue:
    IFB='tdh-ifb-lab'
    DEVICES=('wg-lab','wg-lab2')

    def __init__(self):
        if (os.geteuid()!=0 or os.environ.get('GITHUB_ACTIONS')!='true' or
            os.stat('/proc/self/ns/net').st_ino!=os.stat('/run/netns/'+NAMES[0]).st_ino):
            raise RuntimeError('owned entry namespace required; no host use')
        self.created=[];self.fingerprints={};self.ifindex=None

    def run(self,*args):return command(*args)

    def links(self):return {r['ifname']:r for r in json.loads(self.run('ip','-d','-j','link','show'))}

    def filters(self,dev):return self.run('tc','filter','show','dev',dev,'egress')

    def fingerprint(self,dev):return hashlib.sha256(self.filters(dev).encode()).hexdigest()

    def enable(self):
        if self.created:
            self.check();return 'unchanged'
        links=self.links()
        if self.IFB in links:raise RuntimeError('refusing existing IFB')
        for dev in self.DEVICES:
            if links.get(dev,{}).get('linkinfo',{}).get('info_kind')!='wireguard':raise RuntimeError('both private WG links required')
            rows=json.loads(self.run('tc','-j','qdisc','show','dev',dev))
            if len(rows)!=1 or rows[0]['kind']!='noqueue':raise RuntimeError('refusing custom WG qdisc/classifier')
        try:
            self.run('ip','link','add',self.IFB,'type','ifb')
            self.ifindex=self.links()[self.IFB]['ifindex']
            self.run('ip','link','set',self.IFB,'mtu','1420','txqueuelen','1000','up')
            self.run('tc','qdisc','add','dev',self.IFB,'root','handle','7b00:','htb','default','10')
            self.run('tc','class','add','dev',self.IFB,'parent','7b00:','classid','7b00:10',
                'htb','rate','180mbit','ceil','180mbit','quantum','1514')
            self.run('tc','qdisc','add','dev',self.IFB,'parent','7b00:10','handle','7b01:','fq_codel')
            for dev in self.DEVICES:
                self.run('tc','qdisc','add','dev',dev,'clsact');self.created.append(dev)
                self.run('tc','filter','add','dev',dev,'egress','pref','49152','handle','1','matchall',
                    'action','mirred','egress','redirect','dev',self.IFB)
                self.fingerprints[dev]=self.fingerprint(dev)
            self.check();return 'applied'
        except BaseException:
            self.disable(partial=True)
            raise

    def check(self):
        if self.links().get(self.IFB,{}).get('ifindex')!=self.ifindex:raise RuntimeError('IFB identity changed')
        for dev in self.created:
            if self.fingerprint(dev)!=self.fingerprints[dev]:raise RuntimeError('classifier drift; preserve state')

    def disable(self,partial=False):
        links=self.links()
        if self.ifindex is not None and links.get(self.IFB,{}).get('ifindex')!=self.ifindex:raise RuntimeError('IFB identity changed')
        if not partial and self.created:self.check()
        # Detach redirects before deleting their destination. No blackhole on
        # normal withdrawal; deleting a live IFB first would strand both WGs.
        for dev in reversed(self.created):
            if dev in self.links():
                if dev in self.fingerprints and self.fingerprint(dev)!=self.fingerprints[dev]:raise RuntimeError('classifier drift')
                self.run('tc','qdisc','del','dev',dev,'clsact')
        self.created=[];self.fingerprints={}
        if self.ifindex is not None:
            self.run('ip','link','del',self.IFB);self.ifindex=None
        return 'disabled'

    def snapshot(self):
        return {dev:{kind:self.run('tc','-s','-d',kind,'show','dev',dev) for kind in ('qdisc','class')}
            for dev in (*self.DEVICES,*((self.IFB,) if self.ifindex is not None else ()))}
