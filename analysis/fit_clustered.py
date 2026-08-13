#!/usr/bin/env python3
"""Confirmatory analysis with task-agent-seed blocks as resampling units.

The measurement runner writes six dependent cycle rows per trajectory and
branches the same cycle-one candidate into four cells. This script never treats
those rows, or the four branches of one task-agent-seed block, as independent.
It verifies the replay integrity contract, reduces each trajectory to registered
outcomes, forms within-block grounded-versus-ungrounded contrasts, bootstraps
whole blocks, and computes exact one-sided sign-flip p-values.

    python3 analysis/fit_clustered.py --log results/confirmatory-cycles.jsonl
    python3 analysis/fit_clustered.py --log results/confirmatory-cycles.jsonl --json
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import replay  # noqa: E402


ALPHA = 0.05
BOOTSTRAP_DRAWS = 20_000
BOOTSTRAP_SEED = 20260813
PRIMARY_TESTS = ("B-H1a", "B-H1b")
TASK_TO_TEST = {"s1_swebench": "B-H1a", "s3": "B-H1b"}
EXPECTED_CELLS = {
    "grounded-numeric",
    "grounded-sign",
    "ungrounded-numeric",
    "ungrounded-sign",
}


def require_confirmatory_rows(rows: list[dict]) -> None:
    """Fail closed on apparatus, missing HO-B, or incomplete eligibility."""
    if not rows:
        raise ValueError("trajectory has no cycle rows")
    missing_hob = [row.get("cycle") for row in rows if row.get("delta_hob") is None]
    if missing_hob:
        raise ValueError(f"trajectory lacks delta_hob on cycles {missing_hob}")
    if any(row.get("apparatus_test") for row in rows):
        raise ValueError("apparatus rows cannot enter confirmatory analysis")
    if any(row.get("confirmatory_eligible") is not True for row in rows):
        raise ValueError("every cycle must be confirmatory_eligible")
    planned = rows[0].get("cycles_planned")
    observed = {row.get("cycle") for row in rows}
    if not isinstance(planned, int) or observed != set(range(1, planned + 1)):
        raise ValueError("trajectory is incomplete")


def trajectory_record(rows: list[dict]) -> dict:
    """Reduce one trajectory without treatment-dependent denominators."""
    require_confirmatory_rows(rows)
    head = rows[0]
    n = len(rows)
    harmful = sum(
        1 for row in rows if row.get("accepted") and row["delta_hob"] <= 0
    )
    false_rejections = sum(
        1 for row in rows if not row.get("accepted") and row["delta_hob"] > 0
    )
    final = rows[-1].get("deployed_score_hob")
    baseline = head.get("baseline_score_hob")
    cycle_one = head.get("oracle_score_hob")
    if not all(isinstance(value, (int, float)) for value in (final, baseline, cycle_one)):
        raise ValueError("trajectory lacks baseline, cycle-one, or final HO-B score")
    return {
        "trajectory": head["trajectory"],
        "task": head["task"],
        "agent": head["agent"],
        "seed": head["seed"],
        "cell": head["cell"],
        "grounded": bool(head.get("cell_gate_grounded")),
        "feedback": head.get("cell_feedback"),
        "harmful_acceptance_incidence": harmful / n,
        "false_rejection_incidence": false_rejections / n,
        "final_hob_score": float(final),
        "delivered_hob_gain": float(final) - float(baseline),
        "cycle1_hob_score": float(cycle_one),
        "cycles": n,
    }


def make_blocks(records: list[dict]) -> dict[tuple, list[dict]]:
    """Validate the four-cell randomized block for every task-agent-seed."""
    blocks: dict[tuple, list[dict]] = defaultdict(list)
    for record in records:
        blocks[(record["task"], record["agent"], record["seed"])].append(record)
    for key, block in blocks.items():
        cells = {record["cell"] for record in block}
        if cells != EXPECTED_CELLS or len(block) != len(EXPECTED_CELLS):
            raise ValueError(
                f"block {key!r} has cells {sorted(cells)!r}; expected all four exactly once"
            )
        cycle_one_scores = {record["cycle1_hob_score"] for record in block}
        if len(cycle_one_scores) != 1:
            raise ValueError(f"block {key!r} did not share one cycle-one HO-B candidate")
    return dict(blocks)


def block_difference(block: list[dict], field: str) -> float:
    ungrounded = [record[field] for record in block if not record["grounded"]]
    grounded = [record[field] for record in block if record["grounded"]]
    if len(ungrounded) != 2 or len(grounded) != 2:
        raise ValueError("each block must have two grounded and two ungrounded cells")
    return st.mean(ungrounded) - st.mean(grounded)


def bootstrap_mean_interval(
    values: list[float],
    draws: int = BOOTSTRAP_DRAWS,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float | None, float | None]:
    """Percentile interval resampling complete randomized blocks."""
    if len(values) < 2:
        return None, None
    rng = random.Random(seed)
    n = len(values)
    samples = sorted(st.mean(rng.choices(values, k=n)) for _ in range(draws))
    return samples[int(0.025 * draws)], samples[min(draws - 1, int(0.975 * draws))]


def exact_sign_flip_p(values: list[float], null: float = 0.0) -> float | None:
    """Exact one-sided paired randomization p-value for mean(values) > null."""
    centered = [value - null for value in values]
    if not centered:
        return None
    observed = st.mean(centered)
    extreme = 0
    total = 0
    for signs in itertools.product((-1.0, 1.0), repeat=len(centered)):
        permuted = st.mean(sign * value for sign, value in zip(signs, centered))
        extreme += permuted >= observed - 1e-15
        total += 1
    return extreme / total


def contrast_report(values: list[float]) -> dict:
    return {
        "estimate": st.mean(values) if values else None,
        "ci_95": bootstrap_mean_interval(values),
        "p_one_sided_exact": exact_sign_flip_p(values),
        "blocks": len(values),
        "block_values": values,
    }


def holm(pvalues: dict[str, float], alpha: float = ALPHA) -> dict[str, dict]:
    """Holm step-down over the two prespecified primary tests only."""
    missing = set(PRIMARY_TESTS) - set(pvalues)
    extra = set(pvalues) - set(PRIMARY_TESTS)
    if missing or extra:
        raise ValueError(
            f"primary p-values must be exactly {PRIMARY_TESTS}; missing={sorted(missing)}, "
            f"extra={sorted(extra)}"
        )
    ordered = sorted(pvalues.items(), key=lambda item: item[1])
    verdicts: dict[str, dict] = {}
    rejecting = True
    for index, (name, pvalue) in enumerate(ordered):
        threshold = alpha / (len(ordered) - index)
        reject = rejecting and pvalue <= threshold
        verdicts[name] = {
            "p_value": pvalue,
            "threshold": threshold,
            "reject_null": reject,
        }
        if not reject:
            rejecting = False
    return {name: verdicts[name] for name in PRIMARY_TESTS}


def analyze(log_path: Path) -> dict:
    cycles, abandoned, unparsable = replay.load(log_path)
    integrity = replay.integrity(cycles, abandoned, unparsable)
    if not integrity["clean"]:
        raise ValueError(f"replay integrity is not clean: {integrity}")
    grouped = replay.group_trajectories(cycles, abandoned)
    records = [trajectory_record(rows) for rows in grouped.values()]
    blocks = make_blocks(records)

    task_blocks: dict[str, list[list[dict]]] = defaultdict(list)
    for (task, _agent, _seed), block in blocks.items():
        task_blocks[task].append(block)

    report = {
        "schema_version": 1,
        "source_log": str(log_path),
        "analysis_unit": "task-agent-seed randomized block",
        "trajectories": len(records),
        "blocks": len(blocks),
        "integrity": integrity,
        "tasks": {},
        "primary_tests": {},
        "holm_family": None,
    }
    pvalues = {}
    for task, task_group in sorted(task_blocks.items()):
        harmful = [
            block_difference(block, "harmful_acceptance_incidence")
            for block in task_group
        ]
        false_rejection = [
            block_difference(block, "false_rejection_incidence")
            for block in task_group
        ]
        delivery = [
            block_difference(block, "final_hob_score") for block in task_group
        ]
        gains = [
            block_difference(block, "delivered_hob_gain") for block in task_group
        ]
        report["tasks"][task] = {
            "harmful_acceptance": contrast_report(harmful),
            "false_rejection": contrast_report(false_rejection),
            "final_hob_score": contrast_report(delivery),
            "delivered_hob_gain": contrast_report(gains),
            "cycle1_adjustment": (
                "the four cells share the same cycle-one candidate within each block; "
                "the paired block contrast therefore conditions exactly on its HO-B score"
            ),
        }
        test_name = TASK_TO_TEST.get(task)
        if test_name:
            primary = report["tasks"][task]["harmful_acceptance"]
            report["primary_tests"][test_name] = primary
            pvalues[test_name] = primary["p_one_sided_exact"]

    if set(pvalues) == set(PRIMARY_TESTS) and all(value is not None for value in pvalues.values()):
        report["holm_family"] = holm(pvalues)
    else:
        report["holm_family"] = {
            "status": "not evaluated until both S1 and S3 primary contrasts are complete"
        }
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        report = analyze(args.log)
    except (OSError, ValueError) as exc:
        print(f"analysis refused: {exc}", file=sys.stderr)
        return 1
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    if args.json or not args.output:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
