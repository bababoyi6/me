import json,os,subprocess,sys
from pathlib import Path
from system_vm import ROOT
ports={'base':22040,'entry':22041,'backend1':22042,'backend2':22043}
SSH=['ssh','-i',str(ROOT/'key'),'-o','BatchMode=yes','-o','ConnectTimeout=5','-o','StrictHostKeyChecking=accept-new','-o','UserKnownHostsFile='+str(ROOT/'known_hosts')]
def ssh(node,script,seconds=60):
 remote=f'timeout --signal=TERM --kill-after=10 {max(3,int(seconds)-15)} bash -s'
 return subprocess.run([*SSH,'-p',str(ports[node]),'root@127.0.0.1',remote],input=script,text=True,capture_output=True,timeout=seconds)
def copy(node,source,target):
 return subprocess.run(['scp','-q','-i',str(ROOT/'key'),'-P',str(ports[node]),'-o','BatchMode=yes','-o','StrictHostKeyChecking=accept-new','-o','UserKnownHostsFile='+str(ROOT/'known_hosts'),str(source),'root@127.0.0.1:'+target],capture_output=True,timeout=20)
