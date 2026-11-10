"""Embed existing transaction/installer libraries without their CLI entry points."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BEGIN = '# BEGIN GENERATED CORE UPGRADE\n'
END = '# END GENERATED CORE UPGRADE\n'


def payload():
    chunks = ['HEALTH_AGENT_SOURCE = '+repr((ROOT/'tools/health_agent.py').read_text(encoding='utf-8'))+'\n']
    for name in ('core_install', 'core_transaction', 'core_upgrade'):
        source = (ROOT/'tools'/f'{name}.py').read_text(encoding='utf-8')
        if name != 'core_upgrade':
            source = source.split('\nif __name__ == ', 1)[0]
        else:
            source = '\n'.join(line for line in source.splitlines()
                               if not line.startswith(('from core_install import ', 'from core_transaction import ')))
        chunks.append(source.rstrip()+'\n')
    return '\n'.join(chunks)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    target = ROOT/'telemt-dual-hop.sh'
    content = target.read_text(encoding='utf-8')
    head, tail = content.split(BEGIN)
    _, tail = tail.split(END)
    generated = head+BEGIN+payload()+END+tail
    if args.check:
        if generated != content:
            raise SystemExit('embedded upgrade differs from tested source')
    else:
        target.write_text(generated, encoding='utf-8', newline='\n')
