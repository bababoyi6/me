"""Process-bound CI queue changes; root namespace and config never exposed."""
import json
import sys
from shared_entry_queue import SharedEntryQueue

if __name__=='__main__':
    q=SharedEntryQueue()
    try:
        print(json.dumps({'ready':True}),flush=True)
        for line in sys.stdin:
            request=json.loads(line)
            if request['action']=='enable':result=q.enable()
            elif request['action']=='disable':result=q.disable()
            elif request['action']=='snapshot':result=q.snapshot()
            elif request['action']=='stop':break
            else:raise RuntimeError('unknown action')
            print(json.dumps({'result':result}),flush=True)
    finally:q.disable()
