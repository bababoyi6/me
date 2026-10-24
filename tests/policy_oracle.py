#!/usr/bin/env python3
"""Verify the lab interposer changes only the intended side of private TCP sockets."""
import argparse
import ctypes
import json
import os
import socket


def verify(library):
    lib=ctypes.CDLL(str(library))
    lib.setsockopt.argtypes=(ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_void_p,ctypes.c_uint)
    lib.setsockopt.restype=ctypes.c_int
    expected={}
    with socket.socket() as probe:
        for option in (socket.SO_RCVBUF,socket.SO_SNDBUF):
            probe.setsockopt(socket.SOL_SOCKET,option,262144)
            expected[option]=probe.getsockopt(socket.SOL_SOCKET,option)
    rows=[]
    for mode in ('pass','auto','client-auto','dc-auto','auto-lowat'):
        with socket.socket() as server,socket.socket() as outgoing,socket.socket() as unconnected:
            server.bind(('127.0.0.1',0))
            server.listen(1)
            outgoing.connect(server.getsockname())
            with server.accept()[0] as accepted:
                os.environ['TDH_LAB_SOCKET_MODE']=mode
                os.environ['TDH_LAB_CLIENT_PORT']=str(server.getsockname()[1])
                for role,stream in (('client',accepted),('dc-connected',outgoing),('dc-unconnected',unconnected)):
                    for option in (socket.SO_RCVBUF,socket.SO_SNDBUF):
                        before=stream.getsockopt(socket.SOL_SOCKET,option)
                        value=ctypes.c_int(262144)
                        assert lib.setsockopt(stream.fileno(),socket.SOL_SOCKET,option,ctypes.byref(value),4)==0
                        after=stream.getsockopt(socket.SOL_SOCKET,option)
                        automatic=mode in ('auto','auto-lowat') or mode=='client-auto' and role=='client' or mode=='dc-auto' and role!='client'
                        assert after==(before if automatic else expected[option]),(mode,role,option,before,after)
                        if mode=='auto-lowat':
                            assert stream.getsockopt(socket.IPPROTO_TCP,25)==65536
                        rows.append({'mode':mode,'role':role,'option':option,'before':before,'after':after})
    # Invalid side identity, other request sizes and UDP must pass through.
    for protocol in (socket.SOCK_STREAM,socket.SOCK_DGRAM):
        with socket.socket(socket.AF_INET,protocol) as stream:
            for size,port in ((4096,'12345'),(262144,'invalid')):
                os.environ.update(TDH_LAB_SOCKET_MODE='client-auto',TDH_LAB_CLIENT_PORT=port)
                value=ctypes.c_int(size)
                assert lib.setsockopt(stream.fileno(),socket.SOL_SOCKET,socket.SO_RCVBUF,ctypes.byref(value),4)==0
                after=stream.getsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF)
                stream.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,size)
                assert after==stream.getsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF)
    return {'classification':'PRIVATE_LOOPBACK_SOCKET_POLICY_ORACLE','complete':True,'cases':len(rows),'rows':rows}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library',required=True)
    args=parser.parse_args()
    print(json.dumps(verify(args.library)))
