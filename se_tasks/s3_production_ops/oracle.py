#!/usr/bin/env python3
"""Held-out S3 workload oracle. Do not mount this file in the agent."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
from pathlib import Path


CANARY = "HELD_OUT_ORACLE_S3_2D91F0C7"
# Frozen deterministic workload with boundary, ordinary, and burst requests.
PAYLOAD_LENGTHS = (0, 1, 2, 5, 8, 13, 21, 34, 55, 89, 100, 120, 144, 180, 233) * 8


def percentile_95(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def candidate_contains_canary(candidate_dir: Path) -> bool:
    for path in candidate_dir.rglob("*"):
        if path.is_file() and CANARY in path.read_text(encoding="utf-8", errors="ignore"):
            return True
    return False


def evaluate(candidate_dir: Path) -> dict:
    if candidate_contains_canary(candidate_dir):
        return {"valid": False, "score": 0.0, "metrics": {"canary_leak": True}}
    module_path = candidate_dir / "service.py"
    spec = importlib.util.spec_from_file_location("candidate_service", module_path)
    if spec is None or spec.loader is None:
        return {"valid": False, "score": 0.0, "metrics": {"load_error": True}}
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        return {"valid": True, "score": 0.0, "metrics": {"load_error": type(exc).__name__}}

    errors = 0
    restarts = 0
    consecutive_errors = 0
    work_units = []
    for length in PAYLOAD_LENGTHS:
        try:
            response = module.handle({"payload": "x" * length})
            valid = (
                isinstance(response, dict)
                and response.get("ok") is True
                and response.get("status") == 200
                and isinstance(response.get("work_units"), int)
                and response["work_units"] >= 0
            )
            if not valid:
                raise ValueError("invalid response")
            work_units.append(response["work_units"])
            consecutive_errors = 0
        except Exception:
            errors += 1
            consecutive_errors += 1
            if consecutive_errors == 3:
                restarts += 1
                consecutive_errors = 0

    total = len(PAYLOAD_LENGTHS)
    error_rate = errors / total
    p95 = percentile_95(work_units)
    # Frozen, higher-is-better operational score.
    score = max(0.0, 100.0 - error_rate * 100.0 - restarts * 10.0 - min(p95 / 100.0, 30.0))
    return {
        "valid": True,
        "score": round(score, 6),
        "metrics": {
            "requests": total,
            "errors": errors,
            "error_rate": round(error_rate, 6),
            "p95_logical_work_units": p95,
            "restarts": restarts,
            "canary_leak": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.candidate_dir), sort_keys=True))


if __name__ == "__main__":
    main()
