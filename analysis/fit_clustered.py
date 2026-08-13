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
DESCRIPTIVE_FIELDS = (
    "harmful_acceptance_incidence",
    "false_rejection_incidence",
    "final_hob_score",
    "delivered_hob_gain",
    "erosion_hob",
    "cycles_to_first_positive_hob",
    "first_positive_censor_incidence",
    "gate_mirage_rate",
    "edit_success_rate",
    "input_tokens",
    "output_tokens",
    "agent_seconds",
    "judge_seconds",
    "oracle_seconds",
    "wall_clock_seconds",
    "api_equivalent_usd_lower_bound",
    "api_equivalent_usd",
    "api_equivalent_usd_interval_width",
    "incremental_billed_usd",
    "gain_per_1k_tokens",
    "gain_per_api_equivalent_usd",
    "gain_per_incremental_billed_usd",
    "gain_per_wall_clock_hour",
)


def require_confirmatory_rows(rows: list[dict]) -> None:
    """Fail closed on apparatus, missing HO-B, or incomplete eligibility."""
    if not rows:
        raise ValueError("trajectory has no cycle rows")
    missing_hob = [row.get("cycle") for row in rows if row.get("delta_hob") is None]
    if missing_hob:
        raise ValueError(f"trajectory lacks delta_hob on cycles {missing_hob}")
    missing_hoa = [row.get("cycle") for row in rows if row.get("delta_hoa") is None]
    if missing_hoa:
        raise ValueError(f"trajectory lacks delta_hoa on cycles {missing_hoa}")
    if any(row.get("apparatus_test") for row in rows):
        raise ValueError("apparatus rows cannot enter confirmatory analysis")
    if any(row.get("confirmatory_eligible") is not True for row in rows):
        raise ValueError("every cycle must be confirmatory_eligible")
    if any(
        not isinstance(row.get("candidate_changed"), bool)
        or not isinstance(row.get("agent_completed"), bool)
        or not isinstance(row.get("edit_success"), bool)
        for row in rows
    ):
        raise ValueError("every cycle must carry the frozen edit-success fields")
    if any(
        not isinstance(row.get("api_equivalent_usd_lower_bound"), (int, float))
        or not isinstance(row.get("api_equivalent_usd"), (int, float))
        or row["api_equivalent_usd_lower_bound"] > row["api_equivalent_usd"]
        for row in rows
    ):
        raise ValueError("every cycle must carry a valid shadow-price interval")
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
    best = max(
        [float(baseline)]
        + [float(row["deployed_score_hob"]) for row in rows]
    )
    first_positive = next(
        (row["cycle"] for row in rows if row["delta_hob"] > 0), None
    )
    accepted = [row for row in rows if row.get("accepted")]
    gate_mirage = (
        sum(1 for row in accepted if row["delta_hoa"] <= 0) / len(accepted)
        if accepted
        else None
    )
    input_tokens = sum(float(row.get("input_tokens") or 0.0) for row in rows)
    output_tokens = sum(float(row.get("output_tokens") or 0.0) for row in rows)
    agent_seconds = sum(float(row.get("agent_seconds") or 0.0) for row in rows)
    judge_seconds = sum(float(row.get("judge_seconds") or 0.0) for row in rows)
    oracle_seconds = sum(float(row.get("oracle_seconds") or 0.0) for row in rows)
    wall_clock_seconds = agent_seconds + judge_seconds + oracle_seconds
    api_equivalent_usd = sum(
        float(row.get("api_equivalent_usd") or 0.0) for row in rows
    )
    api_equivalent_usd_lower_bound = sum(
        float(row.get("api_equivalent_usd_lower_bound") or 0.0) for row in rows
    )
    incremental_billed_usd = sum(
        float(row.get("incremental_billed_usd") or 0.0) for row in rows
    )
    delivered_gain = float(final) - float(baseline)
    total_tokens = input_tokens + output_tokens
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
        "delivered_hob_gain": delivered_gain,
        "cycle1_hob_score": float(cycle_one),
        "erosion_hob": best - float(final),
        "cycles_to_first_positive_hob": first_positive or n,
        "cycles_to_first_positive_hob_censored": first_positive is None,
        "first_positive_censor_incidence": float(first_positive is None),
        "gate_mirage_rate": gate_mirage,
        "edit_success_rate": sum(1 for row in rows if row["edit_success"]) / n,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "agent_seconds": agent_seconds,
        "judge_seconds": judge_seconds,
        "oracle_seconds": oracle_seconds,
        "wall_clock_seconds": wall_clock_seconds,
        "api_equivalent_usd_lower_bound": api_equivalent_usd_lower_bound,
        "api_equivalent_usd": api_equivalent_usd,
        "api_equivalent_usd_interval_width": (
            api_equivalent_usd - api_equivalent_usd_lower_bound
        ),
        "incremental_billed_usd": incremental_billed_usd,
        "gain_per_1k_tokens": (
            delivered_gain / (total_tokens / 1000.0) if total_tokens else None
        ),
        "gain_per_api_equivalent_usd": (
            delivered_gain / api_equivalent_usd if api_equivalent_usd else None
        ),
        "gain_per_incremental_billed_usd": (
            delivered_gain / incremental_billed_usd
            if incremental_billed_usd
            else None
        ),
        "gain_per_wall_clock_hour": (
            delivered_gain / (wall_clock_seconds / 3600.0)
            if wall_clock_seconds
            else None
        ),
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
    if not all(isinstance(value, (int, float)) for value in ungrounded + grounded):
        raise ValueError(f"field {field!r} is not estimable in every cell")
    return st.mean(ungrounded) - st.mean(grounded)


def cell_summaries(blocks: list[list[dict]]) -> dict[str, dict]:
    records = [record for block in blocks for record in block]
    summaries = {}
    for cell in sorted(EXPECTED_CELLS):
        cell_records = [record for record in records if record["cell"] == cell]
        summaries[cell] = {"trajectories": len(cell_records)}
        for field in DESCRIPTIVE_FIELDS:
            values = [
                record[field]
                for record in cell_records
                if isinstance(record.get(field), (int, float))
            ]
            summaries[cell][field] = st.mean(values) if values else None
    return summaries


def field_contrast(blocks: list[list[dict]], field: str) -> dict:
    try:
        values = [block_difference(block, field) for block in blocks]
    except ValueError as exc:
        return {"status": str(exc), "blocks": len(blocks)}
    return contrast_report(values)


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
        "schema_version": 2,
        "source_log": str(log_path),
        "analysis_unit": "task-agent-seed randomized block",
        "trajectories": len(records),
        "blocks": len(blocks),
        "integrity": integrity,
        "tasks": {},
        "primary_tests": {},
        "secondary_tests": {},
        "holm_family": None,
    }
    pvalues = {}
    harmful_by_task: dict[str, dict[tuple, float]] = {}
    for task, task_group in sorted(task_blocks.items()):
        harmful = [
            block_difference(block, "harmful_acceptance_incidence")
            for block in task_group
        ]
        harmful_by_task[task] = {
            (block[0]["agent"], block[0]["seed"]): value
            for block, value in zip(task_group, harmful)
        }
        report["tasks"][task] = {
            "harmful_acceptance": contrast_report(harmful),
            "false_rejection": field_contrast(
                task_group, "false_rejection_incidence"
            ),
            "final_hob_score": field_contrast(task_group, "final_hob_score"),
            "delivered_hob_gain": field_contrast(
                task_group, "delivered_hob_gain"
            ),
            "erosion_hob": field_contrast(task_group, "erosion_hob"),
            "cycles_to_first_positive_hob": field_contrast(
                task_group, "cycles_to_first_positive_hob"
            ),
            "first_positive_censor_incidence": field_contrast(
                task_group, "first_positive_censor_incidence"
            ),
            "edit_success_rate": field_contrast(task_group, "edit_success_rate"),
            "cost_and_efficiency": {
                field: field_contrast(task_group, field)
                for field in (
                    "input_tokens",
                    "output_tokens",
                    "agent_seconds",
                    "judge_seconds",
                    "oracle_seconds",
                    "wall_clock_seconds",
                    "api_equivalent_usd_lower_bound",
                    "api_equivalent_usd",
                    "api_equivalent_usd_interval_width",
                    "incremental_billed_usd",
                    "gain_per_1k_tokens",
                    "gain_per_api_equivalent_usd",
                    "gain_per_incremental_billed_usd",
                    "gain_per_wall_clock_hour",
                )
            },
            "cell_means_descriptive": cell_summaries(task_group),
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

    s1 = harmful_by_task.get("s1_swebench")
    b1 = harmful_by_task.get("b1")
    if s1 is not None and b1 is not None and set(s1) == set(b1):
        attenuation = [s1[key] - b1[key] for key in sorted(s1)]
        b_h2 = contrast_report(attenuation)
        b_h2.update(
            {
                "contrast": "S1 grounding gap minus B1 grounding gap",
                "practical_margin": 0.20,
                "estimate_minus_margin": st.mean(attenuation) - 0.20,
                "p_one_sided_exact_at_margin": exact_sign_flip_p(
                    attenuation, null=0.20
                ),
            }
        )
        report["secondary_tests"]["B-H2"] = b_h2
    else:
        report["secondary_tests"]["B-H2"] = {
            "status": "not evaluated until S1 and B1 have identical agent-seed blocks"
        }

    g1 = harmful_by_task.get("g1")
    report["secondary_tests"]["B-G1"] = (
        {
            **contrast_report([g1[key] for key in sorted(g1)]),
            "contrast": "G1 ungrounded minus grounded harmful-acceptance incidence",
        }
        if g1
        else {"status": "not evaluated until G1 blocks are complete"}
    )
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
