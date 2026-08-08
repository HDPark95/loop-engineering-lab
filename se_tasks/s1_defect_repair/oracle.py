#!/usr/bin/env python3
"""Held-out S1 regression oracle. Do not mount this file in the agent."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


CANARY = "HELD_OUT_ORACLE_S1_7E4C1B9A"
CASES = (
    ("1.10", "1.9", True),
    ("2.0", "10.0", False),
    ("1.2", "1.2.0", True),
    ("1.2.0.1", "1.2", True),
    ("0.9.12", "0.10.1", False),
    ("12.1", "2.99", True),
)
MALFORMED = ("1.x", "", "1.-2")


def candidate_contains_canary(candidate_dir: Path) -> bool:
    for path in candidate_dir.rglob("*"):
        if path.is_file() and CANARY in path.read_text(encoding="utf-8", errors="ignore"):
            return True
    return False


def evaluate(candidate_dir: Path) -> dict:
    if candidate_contains_canary(candidate_dir):
        return {"valid": False, "score": 0.0, "metrics": {"canary_leak": True}}
    module_path = candidate_dir / "versioning.py"
    spec = importlib.util.spec_from_file_location("candidate_versioning", module_path)
    if spec is None or spec.loader is None:
        return {"valid": False, "score": 0.0, "metrics": {"load_error": True}}
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        return {"valid": True, "score": 0.0, "metrics": {"load_error": type(exc).__name__}}

    passed = 0
    failures = []
    for current, minimum, expected in CASES:
        try:
            actual = module.is_compatible(current, minimum)
        except Exception as exc:
            failures.append(f"{current}/{minimum}: {type(exc).__name__}")
            continue
        if actual is expected:
            passed += 1
        else:
            failures.append(f"{current}/{minimum}: expected {expected}, got {actual}")

    malformed_passed = 0
    for version in MALFORMED:
        try:
            module.is_compatible(version, "1.0")
        except ValueError:
            malformed_passed += 1
        except Exception as exc:
            failures.append(f"malformed {version!r}: {type(exc).__name__}")
        else:
            failures.append(f"malformed {version!r}: no ValueError")

    total = len(CASES) + len(MALFORMED)
    total_passed = passed + malformed_passed
    return {
        "valid": True,
        "score": round(total_passed / total, 6),
        "metrics": {
            "hidden_passed": total_passed,
            "hidden_total": total,
            "canary_leak": False,
            "failure_count": len(failures),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.candidate_dir), sort_keys=True))


if __name__ == "__main__":
    main()
