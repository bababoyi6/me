"""Keep the single-file manager's opt-in queue controller identical to source."""
import argparse
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BEGIN='# BEGIN GENERATED QUEUE CONTROL\n'
END='# END GENERATED QUEUE CONTROL\n'


def embedded():
    target=ROOT/'telemt-dual-hop.sh'
    content=target.read_text(encoding='utf8')
    head,tail=content.split(BEGIN);_,tail=tail.split(END)
    return head+BEGIN+(ROOT/'tools/queue_control.py').read_text(encoding='utf8').rstrip()+'\n'+END+tail


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true');args=parser.parse_args()
    generated=embedded();target=ROOT/'telemt-dual-hop.sh'
    if args.check:
        if target.read_text(encoding='utf8')!=generated:raise SystemExit('embedded queue controller differs from tested source')
    else:target.write_text(generated,encoding='utf8',newline='\n')
