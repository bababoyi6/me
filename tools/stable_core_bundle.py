"""Deterministic audited archive of the exact 3.5.17 lab candidate; no publishing."""
import argparse
import json
from pathlib import Path
from core_bundle import read_regular, sha256, write_bundle

VERSION='3.5.17+tdh.balanced1'
UPSTREAM='66708d2866f3f3a4541c4fa67984d408e71a8af4'
BUILD_SOURCE='dec285fbdf5b42dd8be3e9e879bc0c2ad8994bc8'
PINS={
    'manifest.json':'0a222c2960b469047cf3adb1521e121c088f7c10756b3d7f407bad3c385271cd',
    'telemt-balanced':'cd4c5ff500d956a8a7059e3e536ff139983a0c5897b4c410ea0e53c68a6cfa14',
    'candidate.patch':'3df2ba260ed5251fc0ba6bc96237bb25ae3cd369aa3b9c718b168e254639aa82',
    'low_rtt_receive.rs':'7b6c638525d3f2f9a106ae0b6c13b8d87fa21a1224ffc32ad5960b002cbb3990',
    'unsent_queue_bulk.rs':'b711a4bf9f3bd8296cb1182e7d956250371f54088264c514a483525f13fef3ca',
    'LICENSE':'c18e6fce5574fb3c5a19c38e2cc875e9101172c98a67aa1b87004752325994e1',
}
NOTICE=b'''Unofficial modified Telemt 3.5.17+tdh.balanced1 x86_64 GNU/Linux lab candidate.
Upstream https://github.com/telemt/telemt commit 66708d2866f3f3a4541c4fa67984d408e71a8af4.
Linux changes: TCP buffer autotuning, existing balanced2 short-RTT receive guard,
and outgoing TCP_NOTSENT_LOWAT=256KiB. No authentication, crypto or relay edits.
The complete source.patch includes both added Linux helper modules.
Original copyright and TELEMT-LICENSE.txt retained. No upstream endorsement.
Exact CI binary repackaged; no bit-for-bit rebuild guarantee is claimed.
Experimental artifact, not a production recommendation or enabled installer channel.
No ARM, official-client, public-VPS, 24-hour or censorship-resistance qualification.
'''


def complete_patch(original,helpers):
    expected={'low_rtt_receive.rs','unsent_queue_bulk.rs'}
    if set(helpers)!=expected or not original.endswith(b'\n'):raise ValueError('incomplete source patch input')
    result=original
    for name in sorted(helpers):
        data=helpers[name]
        if not data or b'\r' in data or not data.endswith(b'\n'):raise ValueError('helper must preserve exact LF source')
        lines=data.splitlines(keepends=True)
        path='src/transport/socket/'+name
        result+=(f'diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n'.encode()
                 +b''.join(b'+'+line for line in lines))
    return result


def bundle(directory,output):
    directory=Path(directory)
    inputs={}
    for name,pin in PINS.items():
        data=read_regular(directory/name,64*1024**2 if name=='telemt-balanced' else 512*1024)
        if sha256(data)!=pin:raise ValueError('candidate input checksum mismatch: '+name)
        inputs[name]=data
    binary=inputs['telemt-balanced'];build=json.loads(inputs['manifest.json'])
    if (binary[:6]!=b'\x7fELF\x02\x01' or binary[18:20]!=b'\x3e\x00' or
        build.get('upstream_commit')!=UPSTREAM or build.get('manager_commit')!=BUILD_SOURCE or
        build.get('binaries',{}).get('balanced',{}).get('version')!='telemt '+VERSION):raise ValueError('candidate provenance mismatch')
    patch=complete_patch(inputs['candidate.patch'],{n:inputs[n] for n in ('low_rtt_receive.rs','unsent_queue_bulk.rs')})
    provenance={'original_build':build,'original_build_manifest_sha256':PINS['manifest.json'],
        'packager_sha256':sha256(Path(__file__).read_bytes()),'packaging_only':True,
        'complete_patch_sha256':sha256(patch),'inputs':PINS}
    files={'telemt':binary,'TELEMT-LICENSE.txt':inputs['LICENSE'],'NOTICE.txt':NOTICE,'source.patch':patch,
        'build-provenance.json':(json.dumps(provenance,sort_keys=True,indent=2)+'\n').encode()}
    manifest={'schema':1,'version':VERSION,'architecture':'x86_64','os':'linux','libc':'gnu','minimum_glibc':'2.35',
        'source_commit':UPSTREAM,'unofficial':True,'channel':'private-experimental','updater_enabled':False,
        'files':{n:{'sha256':sha256(b),'bytes':len(b),'mode':'0755' if n=='telemt' else '0644'} for n,b in files.items()}}
    return {**write_bundle(output,files,manifest),'binary_sha256':PINS['telemt-balanced'],'version':VERSION,
        'complete_patch_sha256':sha256(patch),'published':False,'updater_enabled':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();print(json.dumps(bundle(args.directory,args.output),sort_keys=True))
