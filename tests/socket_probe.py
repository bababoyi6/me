"""Observe numeric skmem fields of a private Telemt child, synthetic data only.

Invoke explicitly with a pinned Telemt binary and JSON output path. Maximum two
simultaneous 20 Mbps, 8 MiB payloads; requires local ss. Not a production command.
"""
import argparse
import concurrent.futures
import json
import re
import signal
import subprocess
import time
from pathlib import Path

from e2e_lab import FullLab

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("telemt", help="pinned official x86_64 GNU binary")
parser.add_argument("output", help="private JSON output path")
args = parser.parse_args()
signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
rows = []
lab = FullLab(args.telemt)
try:
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        transfers = [pool.submit(lab.transfer, "apple.com", direction, 8388608, 2500000) for direction in ("D", "U")]
        for turn in range(3):
            time.sleep(.5)
            lines = subprocess.check_output(["ss", "-tinmpH", "state", "established", "(",
                                             "sport", "=", f":{lab.listen}", "or", "dport", "=",
                                             f":{lab.dc.server_address[1]}", ")"], text=True, timeout=3).splitlines()
            for index, line in enumerate(lines):
                if f"pid={lab.telemt.pid}," not in line:
                    continue
                if f"127.0.0.1:{lab.listen} " in line:
                    role = "client-facing"
                elif f"127.0.0.1:{lab.dc.server_address[1]} " in line:
                    role = "dc-facing"
                else:
                    continue
                stats = " ".join(lines[index + 1:index + 3])
                memory = re.search(r"skmem:\(([^)]+)\)", stats)
                if not memory:
                    continue
                fields = {key: int(value) for key, value in re.findall(r"\b(rb|tb)(\d+)", memory.group(1))}
                if set(fields) != {"rb", "tb"}:
                    raise RuntimeError("missing runtime socket evidence")
                rows.append({"sample": turn + 1, "role": role,
                             "receive_buffer_limit_bytes": fields["rb"], "send_buffer_limit_bytes": fields["tb"]})
        for result in transfers:
            if not result.result(timeout=12)["sha256_verified"]:
                raise RuntimeError("synthetic transfer integrity failure")
    lab.validate(2)
    if len(rows) < 12 or {row["role"] for row in rows} != {"client-facing", "dc-facing"}:
        raise RuntimeError("incomplete socket observations")
    output = {"scope": "numeric buffers for private official Telemt process during two synthetic loopback transfers; no high-RTT performance claim",
              "telemt": lab.version, "sha256_verified_transfers": 2, "rows": rows}
    Path(args.output).write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps({"telemt": lab.version, "observations": len(rows),
                      "receive_limits": sorted({row["receive_buffer_limit_bytes"] for row in rows}),
                      "send_limits": sorted({row["send_buffer_limit_bytes"] for row in rows})}))
finally:
    lab.close()
