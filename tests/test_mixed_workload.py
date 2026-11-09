"""Real private sockets with expiring peers protect the lab teardown deadline."""
import socket
import threading
import time
import unittest

from lab import exact
from mixed_workload import END, finish_idle


class MixedWorkloadTests(unittest.TestCase):
    def test_idle_connections_finish_before_their_peer_deadline(self):
        clients=[];threads=[];errors=[]
        def peer(sock):
            try:
                with sock:
                    sock.settimeout(2)
                    message=exact(sock,64);time.sleep(.1);sock.sendall(message)
                    message=exact(sock,64)
                    if message!=END:raise RuntimeError('invalid end frame')
                    time.sleep(.1);sock.sendall(message)
            except Exception as exc:errors.append(type(exc).__name__)
        try:
            for _ in range(24):
                client,server=socket.socketpair();client.settimeout(3);clients.append(client)
                thread=threading.Thread(target=peer,args=(server,),daemon=True);threads.append(thread);thread.start()
            values=finish_idle(clients)
            self.assertEqual(len(values),24)
            self.assertTrue(all(value>0 for value in values))
        finally:
            for client in clients:client.close()
            for thread in threads:thread.join(timeout=3)
        self.assertFalse(errors)
        self.assertFalse(any(thread.is_alive() for thread in threads))
