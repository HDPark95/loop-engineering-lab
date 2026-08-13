#!/usr/bin/env python3
"""Held-out S1 regression oracle. Do not mount this file in the agent.

The previous version imported the candidate into this process, which put the
candidate and the answer key in one address space. A candidate could read
`CANARY` and `CASES` out of `sys.modules['__main__']` at run time without ever
writing either to disk, so the file-scanning canary check reported clean while
the leak happened. Grading now runs out of process: the parent sends inputs,
the child returns outputs, and the expected values never leave this file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _sandbox.harness import SandboxTimeout, run_calls  # noqa: E402

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


def half_indices(size: int, seed: int, label: str, half: str) -> list[int]:
    """Split one test stratum deterministically and disjointly for a seed."""
    ranked = sorted(
        range(size),
        key=lambda index: hashlib.sha256(
            f"s1|{seed}|{label}|{index}".encode()
        ).hexdigest(),
    )
    parity = 0 if half == "a" else 1
    return sorted(index for rank, index in enumerate(ranked) if rank % 2 == parity)


def candidate_contains_canary(candidate_dir: Path) -> bool:
    for path in candidate_dir.rglob("*"):
        if (
            not path.is_symlink()
            and path.is_file()
            and CANARY in path.read_text(encoding="utf-8", errors="ignore")
        ):
            return True
    return False


def evaluate(candidate_dir: Path, half: str | None = None, seed: int = 0) -> dict:
    if candidate_contains_canary(candidate_dir):
        return {"valid": False, "score": 0.0, "metrics": {"canary_leak": True}}

    # The candidate receives argument pairs and nothing else. Which pairs are
    # ordinary cases and which are malformed is not distinguishable from the
    # call list, and the expected verdicts are never transmitted.
    case_indices = list(range(len(CASES))) if half is None else half_indices(
        len(CASES), seed, "ordinary", half
    )
    malformed_indices = list(range(len(MALFORMED))) if half is None else half_indices(
        len(MALFORMED), seed, "malformed", half
    )
    selected_cases = [CASES[index] for index in case_indices]
    selected_malformed = [MALFORMED[index] for index in malformed_indices]
    calls = [[current, minimum] for current, minimum, _ in selected_cases]
    calls += [[version, "1.0"] for version in selected_malformed]
    try:
        outcome = run_calls(candidate_dir, "versioning", "is_compatible", calls, unpack=True)
    except SandboxTimeout:
        return {"valid": False, "score": 0.0, "metrics": {"timeout": True}}

    if not outcome.get("ok"):
        return {
            "valid": True,
            "score": 0.0,
            "metrics": {"load_error": outcome.get("load_error", "Unknown")},
        }

    results = outcome.get("results", [])
    failures = []
    passed = 0
    for index, (current, minimum, expected) in enumerate(selected_cases):
        result = results[index] if index < len(results) else {"error": "MissingResult"}
        if "error" in result:
            failures.append(f"{current}/{minimum}: {result['error']}")
            continue
        actual = result.get("value")
        if actual is expected:
            passed += 1
        else:
            failures.append(f"{current}/{minimum}: expected {expected}, got {actual}")

    malformed_passed = 0
    for offset, version in enumerate(selected_malformed):
        index = len(selected_cases) + offset
        result = results[index] if index < len(results) else {"error": "MissingResult"}
        if "error" in result:
            if result["error"] == "ValueError":
                malformed_passed += 1
            else:
                failures.append(f"malformed {version!r}: {result['error']}")
        else:
            failures.append(f"malformed {version!r}: no ValueError")

    total = len(selected_cases) + len(selected_malformed)
    total_passed = passed + malformed_passed
    return {
        "valid": True,
        "score": round(total_passed / total, 6),
        "metrics": {
            "hidden_passed": total_passed,
            "hidden_total": total,
            "canary_leak": False,
            "failure_count": len(failures),
            "oracle_half": half or "full",
            "split_seed": seed if half else None,
            "case_indices": case_indices,
            "malformed_indices": malformed_indices,
            "traced_lines": (outcome.get("cost") or {}).get("traced_lines"),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--half", choices=("a", "b"))
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.candidate_dir, args.half, args.seed), sort_keys=True))


if __name__ == "__main__":
    main()
