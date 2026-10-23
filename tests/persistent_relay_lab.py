"""Bounded loopback liveness test with a DC that waits for application replies.

Download digest/EOF are withheld until the client acknowledges all payload bytes,
so closing the DC cannot accidentally rescue a buffered tail. No Telegram files.
"""
import argparse
import json
from pathlib import Path
import re
import signal
import time

from e2e_lab import FullLab


def main(args):
    rows, dc_results = [], []
    for label, binary, experiment in (
        ("official", args.baseline, None),
        ("candidate", args.candidate, (args.candidate_sha256, args.candidate_version)),
    ):
        lab = FullLab(binary, args.haproxy, experimental_binary=experiment)
        try:
            for persistent in (False, True):
                for direction in ("D", "U"):
                    for size in (65536, 8388608):
                        row = {"build": label, "telemt": lab.version, "persistent_dc": persistent,
                               "direction": direction, "bytes": size}
                        started = time.monotonic()
                        try:
                            result = lab.transfer("apple.com", direction, size, 3125000,
                                                  io_timeout=3, persistent=persistent)
                            row.update(complete=True, result=result)
                        except Exception as exc:
                            row.update(complete=False, exception=type(exc).__name__,
                                       phase=getattr(exc, "lab_phase", "unknown"),
                                       received_bytes=getattr(exc, "lab_received_bytes", None))
                        row["elapsed_seconds"] = time.monotonic() - started
                        rows.append(row)
                        Path(args.output).write_text(json.dumps({"scope": __doc__, "rows": rows}, indent=2) + "\n")
            if label == "candidate":
                lab.validate(8)
        finally:
            # Baseline failures may intentionally leave an incomplete DC exchange.
            with lab.dc.error_lock:
                dc_results.append({"build": label, "completed": lab.dc.completed, "errors": list(lab.dc.errors)})
            lab.close()
            Path(args.output).write_text(json.dumps({"scope": __doc__, "rows": rows,
                                                    "dc_results": dc_results}, indent=2) + "\n")
    if any(not row["complete"] for row in rows if row["build"] == "candidate"):
        raise RuntimeError("experimental persistent relay failed")
    print(json.dumps({"complete": sum(row["complete"] for row in rows), "cases": len(rows)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--candidate-sha256", required=True)
    parser.add_argument("--candidate-version", choices=("telemt 3.5.5+tdh.audit1", "telemt 3.5.5+tdh.audit2"),
                        default="telemt 3.5.5+tdh.audit2")
    parser.add_argument("--haproxy", default="haproxy")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{64}", args.candidate_sha256):
        parser.error("explicit SHA256 required")
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    main(args)
