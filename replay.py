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

The primary quantity is delivered outcome and regression acceptance on HO-B.
Legacy pre-split logs label the grounded mirage rate structural. Confirmatory
logs carry `delta_hob`, so a grounded HO-A gate can make an empirical error on
HO-B and the outcome is no longer fixed by the gate rule.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median


def load(log_path: Path) -> tuple[list[dict], list[dict], list[int]]:
    """Load valid records and retain the line numbers of corrupt JSONL rows."""
    cycles, abandoned, unparsable = [], [], []
    with log_path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                unparsable.append(number)
                continue
            (abandoned if record.get("abandoned") else cycles).append(record)
    return cycles, abandoned, unparsable


def attempt_identity(record: dict) -> tuple[str, str]:
    attempt = record.get("attempt_id") or record.get("run_id") or "legacy"
    return record["trajectory"], attempt


def group_trajectories(
    cycles: list[dict], abandoned: list[dict] | None = None
) -> dict[str, list[dict]]:
    """Group complete attempts only; retries can never be merged or double-counted."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    abandoned_identities = {
        attempt_identity(record) for record in (abandoned or []) if record.get("trajectory")
    }
    for record in cycles:
        if not record.get("trajectory"):
            continue
        trajectory, attempt = attempt_identity(record)
        if (trajectory, attempt) in abandoned_identities:
            continue
        grouped[f"{trajectory}|attempt={attempt}"].append(record)
    for rows in grouped.values():
        rows.sort(key=lambda r: r.get("cycle", 0))
    return grouped


def trajectory_metrics(rows: list[dict]) -> dict:
    graded = [r for r in rows if r.get("oracle_delta") is not None]
    accepted = [r for r in graded if r.get("accepted")]
    positive = [r for r in graded if r["oracle_delta"] > 0]
    rejected = [r for r in graded if not r.get("accepted")]
    baseline = rows[0].get("baseline_score")
    final = rows[-1].get("deployed_score")
    planned = rows[0].get("cycles_planned")
    denominator = planned if isinstance(planned, int) and planned > 0 else len(rows)
    first_positive = next((r["cycle"] for r in graded if r["oracle_delta"] > 0), None)
    return {
        # Primary: what the loop actually delivered.
        "delivered_gain": (
            None if baseline is None or final is None else round(final - baseline, 6)
        ),
        "final_deployed_score": final,
        "baseline_score": baseline,
        # Descriptive. Structural in the grounded arms; see the module docstring.
        "mirage_rate": (
            round(sum(1 for r in accepted if r["oracle_delta"] <= 0) / len(accepted), 6)
            if accepted
            else None
        ),
        "regression_acceptance_rate": (
            round(sum(1 for r in accepted if r["oracle_delta"] <= 0) / len(accepted), 6)
            if accepted
            else None
        ),
        # Confirmatory fixed-denominator quantities. A cell with no accepted or
        # no positive-delta cycles contributes zero instead of disappearing.
        "harmful_acceptance_incidence": (
            round(
                sum(1 for r in graded if r.get("accepted") and r["oracle_delta"] <= 0)
                / denominator,
                6,
            )
            if denominator
            else None
        ),
        "false_rejection_rate": (
            round(sum(1 for r in rejected if r["oracle_delta"] > 0) / len(positive), 6)
            if positive
            else None
        ),
        "false_rejection_incidence": (
            round(
                sum(1 for r in graded if not r.get("accepted") and r["oracle_delta"] > 0)
                / denominator,
                6,
            )
            if denominator
            else None
        ),
        "cycles_to_first_positive": first_positive,
        "right_censored": first_positive is None,
        "accepted_cycles": len(accepted),
        "ungraded_cycles": len(rows) - len(graded),
        "outcome_is_hob": all("delta_hob" in row for row in graded),
        "cycles": len(rows),
        "input_tokens": sum(r.get("input_tokens") or 0 for r in rows),
        "output_tokens": sum(r.get("output_tokens") or 0 for r in rows),
        "api_equivalent_usd": round(
            sum(
                r.get("api_equivalent_usd")
                if r.get("api_equivalent_usd") is not None
                else (r.get("usd") or 0.0)
                for r in rows
            ),
            6,
        ),
        "incremental_billed_usd": round(
            sum(r.get("incremental_billed_usd") or 0.0 for r in rows), 6
        ),
        "agent_seconds": round(sum(r.get("agent_seconds") or 0.0 for r in rows), 3),
    }


def integrity(cycles: list[dict], abandoned: list[dict], unparsable: list[int]) -> dict:
    missing_trajectory_records = sum(
        1 for record in [*cycles, *abandoned] if not record.get("trajectory")
    )
    identified_cycles = [record for record in cycles if record.get("trajectory")]
    identified_abandoned = [record for record in abandoned if record.get("trajectory")]
    leaks = [r["trajectory"] for r in identified_cycles if r.get("canary_leak")]
    mismatches = [
        r["trajectory"]
        for r in identified_cycles
        if r.get("model_identity_matches") is False
        or (
            r.get("schema_version", 0) >= 2
            and (
                not r.get("model_served")
                or r.get("model_identity_matches") is not True
            )
        )
    ]
    invalid_oracles = [
        r["trajectory"] for r in identified_cycles if r.get("oracle_valid") is False
    ]
    missing_archives = [
        r["trajectory"]
        for r in identified_cycles
        if r.get("schema_version", 0) >= 3
        and not r.get("apparatus_test")
        and not r.get("candidate_archive_manifest_sha256")
    ]
    reward_hacks = [
        r["trajectory"] for r in identified_cycles if r.get("reward_hack_signals")
    ]
    ungraded = [
        r["trajectory"] for r in identified_cycles if r.get("oracle_delta") is None
    ]
    schema_two_rows = [
        r
        for r in [*identified_cycles, *identified_abandoned]
        if r.get("schema_version", 0) >= 2
    ]
    manifest_digests = {r.get("manifest_digest") for r in schema_two_rows}
    preregistration_commits = {r.get("preregistration_commit") for r in schema_two_rows}
    manifest_mixed = len(manifest_digests) > 1 or None in manifest_digests
    preregistration_mixed = (
        len(preregistration_commits) > 1 or None in preregistration_commits
    )
    incomplete = []
    completed_tokens = set()
    grouped = group_trajectories(identified_cycles, identified_abandoned)
    for rows in grouped.values():
        planned = rows[0].get("cycles_planned")
        if isinstance(planned, int):
            if {r.get("cycle") for r in rows} == set(range(1, planned + 1)):
                completed_tokens.add(rows[0]["trajectory"])
            else:
                incomplete.append(rows[0]["trajectory"])
    abandoned_tokens = {r["trajectory"] for r in identified_abandoned}
    recovered_abandoned = abandoned_tokens & completed_tokens
    unrecovered_abandoned = abandoned_tokens - completed_tokens
    return {
        "canary_leak_trajectories": sorted(set(leaks)),
        "model_identity_mismatch_trajectories": sorted(set(mismatches)),
        "abandoned_trajectories": sorted(abandoned_tokens),
        "recovered_abandoned_trajectories": sorted(recovered_abandoned),
        "unrecovered_abandoned_trajectories": sorted(unrecovered_abandoned),
        "invalid_oracle_trajectories": sorted(set(invalid_oracles)),
        "missing_candidate_archive_trajectories": sorted(set(missing_archives)),
        "reward_hack_signal_trajectories": sorted(set(reward_hacks)),
        "ungraded_trajectories": sorted(set(ungraded)),
        "incomplete_trajectories": sorted(set(incomplete)),
        "manifest_digests": sorted(value for value in manifest_digests if value),
        "mixed_or_missing_manifest_digest": manifest_mixed,
        "preregistration_commits": sorted(
            value for value in preregistration_commits if value
        ),
        "mixed_or_missing_preregistration_commit": preregistration_mixed,
        "unparsable_log_lines": unparsable,
        "missing_trajectory_records": missing_trajectory_records,
        "clean": not any(
            (
                leaks,
                mismatches,
                unrecovered_abandoned,
                invalid_oracles,
                missing_archives,
                reward_hacks,
                ungraded,
                incomplete,
                manifest_mixed,
                preregistration_mixed,
                unparsable,
                missing_trajectory_records,
            )
        ),
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
        regressions = [
            m["regression_acceptance_rate"]
            for m in metrics
            if m["regression_acceptance_rate"] is not None
        ]
        false_rejections = [
            m["false_rejection_rate"]
            for m in metrics
            if m["false_rejection_rate"] is not None
        ]
        harmful_incidences = [
            m["harmful_acceptance_incidence"]
            for m in metrics
            if m["harmful_acceptance_incidence"] is not None
        ]
        false_rejection_incidences = [
            m["false_rejection_incidence"]
            for m in metrics
            if m["false_rejection_incidence"] is not None
        ]
        api_equivalent_usd = sum(m["api_equivalent_usd"] for m in metrics)
        incremental_billed_usd = sum(m["incremental_billed_usd"] for m in metrics)
        tokens = sum(m["input_tokens"] + m["output_tokens"] for m in metrics)
        grounded = cell.startswith("grounded")
        outcome_is_hob = all(m["outcome_is_hob"] for m in metrics)
        out[f"{task}|{agent}|{cell}"] = {
            "trajectories": len(metrics),
            # Analysis unit is the trajectory. Cycles inside one trajectory share
            # state and are not independent; treating them as such is
            # pseudoreplication and inflates every interval.
            "delivered_gain_median": round(median(gains), 6) if gains else None,
            "delivered_gain_values": gains,
            "mirage_rate_median": round(median(mirages), 6) if mirages else None,
            "regression_acceptance_rate_median": (
                round(median(regressions), 6) if regressions else None
            ),
            "false_rejection_rate_median": (
                round(median(false_rejections), 6) if false_rejections else None
            ),
            "harmful_acceptance_incidence_median": (
                round(median(harmful_incidences), 6) if harmful_incidences else None
            ),
            "false_rejection_incidence_median": (
                round(median(false_rejection_incidences), 6)
                if false_rejection_incidences
                else None
            ),
            "mirage_rate_is_structural": grounded and not outcome_is_hob,
            "outcome_is_hob": outcome_is_hob,
            "gain_per_1k_tokens": (
                round(sum(gains) / (tokens / 1000.0), 6) if gains and tokens else None
            ),
            "gain_per_api_equivalent_usd": (
                round(sum(gains) / api_equivalent_usd, 6)
                if gains and api_equivalent_usd
                else None
            ),
            "gain_per_incremental_billed_usd": (
                round(sum(gains) / incremental_billed_usd, 6)
                if gains and incremental_billed_usd
                else None
            ),
            "api_equivalent_usd": round(api_equivalent_usd, 6),
            "incremental_billed_usd": round(incremental_billed_usd, 6),
        }
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    cycles, abandoned, unparsable = load(args.log)
    grouped = group_trajectories(cycles, abandoned)
    report = {
        "schema_version": 2,
        "source_log": str(args.log),
        "cycle_records": len(cycles),
        "trajectories": len(grouped),
        "integrity": integrity(cycles, abandoned, unparsable),
        "by_cell": by_cell(grouped),
        "note": (
            "Primary quantity is delivered_gain on HO-B. mirage_rate is structural "
            "only for legacy pre-split grounded logs; confirmatory rows carry "
            "delta_hob and are empirical outcomes."
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
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
