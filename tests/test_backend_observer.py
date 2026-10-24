"""Two tiny CI-only real-kernel backend socket observations; no Telemt load."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import unittest

from kernel_observer import SOCKET_ENDPOINTS, observe_sockets


def child(namespace):
    address,_=SOCKET_ENDPOINTS[namespace]
    with socket.socket() as listener,socket.socket() as client:
        listener.bind((address,0));listener.listen(1)
        port=listener.getsockname()[1]
        client.connect((address,port))
        with listener.accept()[0] as accepted:
            payload=b'backend-observer'
            client.sendall(payload)
            if accepted.recv(64)!=payload:
                raise RuntimeError('private fixture payload mismatch')
            try:
                observe_sockets(os.getpid(),port)
            except OSError:
                # The legacy client namespace may be absent in this minimal fixture.
                pass
            else:
                raise RuntimeError('legacy client scope accepted a backend namespace')
            rows=observe_sockets(os.getpid(),port,namespace=namespace)
            if (len(rows)!=1 or rows[0]['role']!='client' or
                    rows[0]['rb']!=accepted.getsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF) or
                    rows[0]['bytes_received']!=len(payload)):
                raise RuntimeError('real backend endpoint/inode or kernel-buffer observation mismatch')
            return {'namespace':namespace,'bytes':len(payload),'sample':rows[0]}


@unittest.skipUnless(sys.platform=='linux' and getattr(os,'geteuid',lambda:-1)()==0,
                     'requires root on the ephemeral Linux CI runner')
class BackendObserverTests(unittest.TestCase):
    def test_actual_backend_namespace_sockets_and_legacy_scope_rejection(self):
        # Fail rather than reuse or remove another test's namespace.
        existing=subprocess.check_output(['ip','netns','list'],text=True,timeout=3)
        self.assertFalse(any(line.split()[0] in ('tdh-audit-b','tdh-audit-b2') for line in existing.splitlines()))
        for namespace in ('tdh-audit-b','tdh-audit-b2'):
            created=False
            try:
                subprocess.run(['ip','netns','add',namespace],check=True,timeout=3)
                created=True
                subprocess.run(['ip','-n',namespace,'link','set','lo','up'],check=True,timeout=3)
                address=SOCKET_ENDPOINTS[namespace][0]
                subprocess.run(['ip','-n',namespace,'addr','add',address+'/32','dev','lo'],check=True,timeout=3)
                result=json.loads(subprocess.check_output(['ip','netns','exec',namespace,sys.executable,
                    str(Path(__file__).resolve()),'--child',namespace],text=True,timeout=5))
                self.assertEqual(result['namespace'],namespace)
                self.assertEqual(result['bytes'],16)
            finally:
                if created:
                    subprocess.run(['ip','netns','del',namespace],check=True,timeout=3)


if __name__=='__main__' and len(sys.argv)==3 and sys.argv[1]=='--child':
    print(json.dumps(child(sys.argv[2])))
