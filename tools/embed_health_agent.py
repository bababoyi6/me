#!/usr/bin/env python3
"""Keep the single-file installer health agent identical to the reviewed source."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
START = "  cat >/usr/local/lib/telemt-dual-hop/health_agent.py <<'PY'\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    installer = ROOT / "telemt-dual-hop.sh"
    text = installer.read_text(encoding="utf-8")
    start = text.index(START) + len(START)
    end = text.index("\nPY\n", start)
    source = (ROOT / "tools/health_agent.py").read_text(encoding="utf-8").rstrip("\n")
    if text[start:end] == source:
        return 0
    if args.check:
        print("health agent source differs from installer")
        return 1
    installer.write_text(text[:start] + source + text[end:], encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
