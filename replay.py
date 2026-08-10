#!/usr/bin/env python3
"""Recompute every reported quantity from the cycle log alone.

The point is that a reviewer can check the arithmetic without rerunning a single
agent, and that changing the analysis never costs another dollar of measurement.
Nothing here calls a model, an oracle or a network.

It also refuses to average over a defect. Three conditions void a run and the
tool reports them instead of quietly folding them into a mean:

  canary leak            grading data reached the candidate
  model identity mismatch the runtime served something other than the manifest pin
  abandoned trajectory    a trajectory stopped partway and its records remain

The primary quantity is deliberately not the mirage rate. A grounded gate accepts
a cycle when the delta is positive, and a mirage is an accepted cycle whose delta
is at most zero, so the grounded arms cannot produce one. Reporting that
difference restates the acceptance rule. Delivered outcome is reported first, the
mirage rate is reported as a descriptive statistic, and the grounded arms are
labelled structural so nobody reads a definitional zero as a finding.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import median


def load(log_path: Path) -> tuple[list[dict], list[dict]]:
    cycles, abandoned = [], []
    with log_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            (abandoned if record.get("abandoned") else cycles).append(record)
    return cycles, abandoned


def group_trajectories(cycles: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for record in cycles:
        grouped[record["trajectory"]].append(record)
    for rows in grouped.values():
        rows.sort(key=lambda r: r["cycle"])
    return grouped


def trajectory_metrics(rows: list[dict]) -> dict:
    accepted = [r for r in rows if r.get("accepted")]
    positive = [r for r in rows if (r.get("oracle_delta") or 0.0) > 0]
    rejected = [r for r in rows if not r.get("accepted")]
    baseline = rows[0].get("baseline_score")
    final = rows[-1].get("deployed_score")
    first_positive = next((r["cycle"] for r in rows if (r.get("oracle_delta") or 0.0) > 0), None)
    return {
        # Primary: what the loop actually delivered.
        "delivered_gain": (
            None if baseline is None or final is None else round(final - baseline, 6)
        ),
        "final_deployed_score": final,
        "baseline_score": baseline,
        # Descriptive. Structural in the grounded arms; see the module docstring.
        "mirage_rate": (
            round(sum(1 for r in accepted if (r.get("oracle_delta") or 0.0) <= 0) / len(accepted), 6)
            if accepted
            else None
        ),
        "regression_acceptance_rate": (
            round(sum(1 for r in accepted if (r.get("oracle_delta") or 0.0) < 0) / len(accepted), 6)
            if accepted
            else None
        ),
        "false_rejection_rate": (
            round(sum(1 for r in rejected if (r.get("oracle_delta") or 0.0) > 0) / len(positive), 6)
            if positive
            else None
        ),
        "cycles_to_first_positive": first_positive,
        "right_censored": first_positive is None,
        "accepted_cycles": len(accepted),
        "cycles": len(rows),
        "input_tokens": sum(r.get("input_tokens") or 0 for r in rows),
        "output_tokens": sum(r.get("output_tokens") or 0 for r in rows),
        "usd": round(sum(r.get("usd") or 0.0 for r in rows), 6),
        "agent_seconds": round(sum(r.get("agent_seconds") or 0.0 for r in rows), 3),
    }


def integrity(cycles: list[dict], abandoned: list[dict]) -> dict:
    leaks = [r["trajectory"] for r in cycles if r.get("canary_leak")]
    mismatches = [
        r["trajectory"] for r in cycles if r.get("model_identity_matches") is False
    ]
    return {
        "canary_leak_trajectories": sorted(set(leaks)),
        "model_identity_mismatch_trajectories": sorted(set(mismatches)),
        "abandoned_trajectories": sorted({r["trajectory"] for r in abandoned}),
        "clean": not leaks and not mismatches and not abandoned,
    }


def by_cell(grouped: dict[str, list[dict]]) -> dict:
    cells: dict[tuple, list[dict]] = defaultdict(list)
    for rows in grouped.values():
        head = rows[0]
        cells[(head["task"], head["agent"], head["cell"])].append(trajectory_metrics(rows))

    out = {}
    for (task, agent, cell), metrics in sorted(cells.items()):
        gains = [m["delivered_gain"] for m in metrics if m["delivered_gain"] is not None]
        mirages = [m["mirage_rate"] for m in metrics if m["mirage_rate"] is not None]
        usd = sum(m["usd"] for m in metrics)
        tokens = sum(m["input_tokens"] + m["output_tokens"] for m in metrics)
        grounded = cell.startswith("grounded")
        out[f"{task}|{agent}|{cell}"] = {
            "trajectories": len(metrics),
            # Analysis unit is the trajectory. Cycles inside one trajectory share
            # state and are not independent; treating them as such is
            # pseudoreplication and inflates every interval.
            "delivered_gain_median": round(median(gains), 6) if gains else None,
            "delivered_gain_values": gains,
            "mirage_rate_median": round(median(mirages), 6) if mirages else None,
            "mirage_rate_is_structural": grounded,
            "gain_per_1k_tokens": (
                round(sum(gains) / (tokens / 1000.0), 6) if gains and tokens else None
            ),
            "gain_per_usd": round(sum(gains) / usd, 6) if gains and usd else None,
            "usd": round(usd, 6),
        }
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    cycles, abandoned = load(args.log)
    grouped = group_trajectories(cycles)
    report = {
        "schema_version": 1,
        "source_log": str(args.log),
        "cycle_records": len(cycles),
        "trajectories": len(grouped),
        "integrity": integrity(cycles, abandoned),
        "by_cell": by_cell(grouped),
        "note": (
            "Primary quantity is delivered_gain. mirage_rate is descriptive and is "
            "structurally zero wherever mirage_rate_is_structural is true, because "
            "the gate accepts exactly the cycles the mirage definition excludes."
        ),
    }
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(text)
    if not report["integrity"]["clean"]:
        print(
            "integrity problems present; the affected trajectories must be excluded "
            "or rerun before these numbers are reported",
            file=__import__("sys").stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
