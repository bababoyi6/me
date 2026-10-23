"""Keep the single-file installer synchronized with its tested Python source."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BEGIN = '# BEGIN GENERATED CORE INSTALLER\n'
END = '# END GENERATED CORE INSTALLER\n'


def generated():
    script = (ROOT/'telemt-dual-hop.sh').read_text(encoding='utf8')
    if script.count(BEGIN) != 1 or script.count(END) != 1:
        raise ValueError('unique embedded installer markers required')
    head, tail = script.split(BEGIN)
    _, tail = tail.split(END)
    return head+BEGIN+(ROOT/'tools/core_install.py').read_text(encoding='utf8').rstrip()+'\n'+END+tail


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    content = generated().encode()
    if args.check:
        if content != (ROOT/'telemt-dual-hop.sh').read_bytes():
            raise SystemExit('embedded installer differs from tested source')
    else:
        (ROOT/'telemt-dual-hop.sh').write_bytes(content)
