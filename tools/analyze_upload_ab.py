#!/usr/bin/env python3
"""Summarize paired uploads without discarding failures or combining environments."""
import argparse
import json
import math
from pathlib import Path
import statistics


def summarize(data):
    control = data.get('control_build', 'flush')
    candidate = data.get('candidate_build', 'auto')
    if (control, candidate) not in (('flush', 'auto'), ('control', 'fair')):
        raise ValueError('unknown native core pair')
    groups = []
    for streams in sorted({row["streams"] for row in data["rows"]}):
        selected = [row for row in data["rows"] if row["streams"] == streams]
        builds = {}
        for build in (control, candidate):
            trials = [row for row in selected if row["build"] == build]
            valid, failed, failed_probes = [], 0, 0
            cpu, memory, probes, retransmits, windows = [], [], [], [], []
            for record in trials:
                rows = record.get("trial", {}).get("rows", [])
                if record.get("exit") != 0 or len(rows) != 1 or not rows[0].get("complete") or not rows[0].get("measurement_complete", True):
                    failed += 1
                    continue
                trial = rows[0]
                if trial.get('build', build) != build:
                    raise ValueError('core identity mismatch within one A/B experiment')
                failed_probes += len(trial.get("small_probe_errors", []))
                if trial["streams"] != streams or trial["direction"] != "U" or trial["expected_payload_bytes"] != data["payload_bytes_total_per_trial"]:
                    raise ValueError("workload mismatch within one A/B experiment")
                if trial["link_rate_mbps"] != data["rate_mbps_per_direction"]:
                    raise ValueError("rate mismatch within one A/B experiment")
                if not isinstance(trial["mbps"], (int, float)) or isinstance(trial["mbps"], bool) or not math.isfinite(trial["mbps"]) or trial["mbps"] <= 0:
                    raise ValueError("invalid throughput")
                valid.append(trial["mbps"])
                cpu.append(trial["telemt_cpu_seconds"])
                memory.append(trial["maximum_telemt_rss_sampled_bytes"])
                probes.extend(value["elapsed_seconds"] * 1000 for value in trial.get("small_probes", []))
                before, after = trial.get("network_counters_before", {}), trial.get("network_counters_after", {})
                key = "client_namespace_tcp"
                if key in before and key in after:
                    retransmits.append(after[key]["RetransSegs"] - before[key]["RetransSegs"])
                windows.extend(value["rb"] for value in trial.get("socket_samples", []) if value.get("role") == "dc" and "rb" in value)
            builds[build] = {"successful_trials": len(valid), "failed_trials": failed,
                             "failed_probes": failed_probes,
                             "median_mbps": statistics.median(valid) if valid else None,
                             "median_telemt_cpu_seconds": statistics.median(cpu) if cpu else None,
                             "max_sampled_telemt_rss_bytes": max(memory, default=None),
                             "median_small_probe_ms": statistics.median(probes) if probes else None,
                             "median_namespace_retransmits": statistics.median(retransmits) if retransmits else None,
                             "max_sampled_dc_receive_limit_bytes": max(windows, default=None)}
        # Never publish a speed ratio if a pair is missing/failed or a probe failed.
        paired = data["complete"] and len(selected) == data["rounds"] * 2 and all(builds[b]["failed_trials"] == 0 and
                 builds[b]["failed_probes"] == 0 and builds[b]["successful_trials"] == data["rounds"] and
                 {r["pair_round"] for r in selected if r["build"] == b} == set(range(1, data["rounds"]+1)) for b in builds)
        a, b = builds[control]["median_mbps"], builds[candidate]["median_mbps"]
        change = 100*(b/a-1) if paired and a and b else None
        groups.append({"streams": streams, "builds": builds, "complete_pairs": paired,
                       "candidate_change_percent": change,
                       "auto_change_percent": change if candidate == 'auto' else None})
    return {"classification": data["classification"], "scope": data["scope"],
            "complete": data["complete"], "groups": groups,
            "interpretation": "Small probe includes handshake; retransmission is namespace aggregate, not proof of loss cause."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(json.loads(args.result.read_text())), indent=2))
