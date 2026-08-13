#!/usr/bin/env python3
"""Held-out synthetic-user world-state oracle for G1."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
from pathlib import Path


COHORT_SIZE = 2000
MAX_FILE_BYTES = 64_000
FILES = ("landing.html", "funnel.json", "copy.txt")
CTA = re.compile(
    r"<button\b|<input[^>]+type=['\"]?submit|role=['\"]?button|"
    r"sign[\s-]?up|get[\s-]?started|start\s+(free|now|today)|"
    r"create\s+(an\s+)?account|join\s+(now|free|today)|register|try\s+(it\s+)?free",
    re.I,
)
FORM = re.compile(r"<form\b", re.I)
EMAIL = re.compile(r"<input[^>]+(?:type=['\"]?email|name=['\"]?email)", re.I)
HEAD = re.compile(r"<head\b.*?</head>", re.I | re.S)


def read_candidate(root: Path) -> tuple[str, list[str], str]:
    values = []
    for name in FILES:
        path = root / name
        if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f"missing, linked, or oversized G1 artifact: {name}")
        values.append(path.read_text(encoding="utf-8"))
    landing, funnel_text, copy = values
    parsed = json.loads(funnel_text)
    steps = parsed.get("steps")
    if not isinstance(steps, list) or any(not isinstance(step, str) for step in steps):
        raise ValueError("funnel.json must contain a string steps list")
    return landing, steps, copy


def observed_features(root: Path) -> dict:
    landing, steps, copy = read_candidate(root)
    body = HEAD.sub(" ", landing)
    words = len(copy.split())
    return {
        "landing_bytes": len(landing.encode()),
        "steps": len(steps),
        "cta": int(bool(CTA.search(body))),
        "form": int(bool(FORM.search(body))),
        "email": int(bool(EMAIL.search(body))),
        "copy_words": words,
        "broken_markup": int(landing.count("<") != landing.count(">")),
    }


def conversion_probability(features: dict) -> float:
    load_penalty = min(features["landing_bytes"] / 5000.0, 2.0)
    step_penalty = max(0, features["steps"] - 2) / 5.0
    copy_quality = max(0.0, 1.0 - abs(features["copy_words"] - 65) / 100.0)
    z = (
        -1.65
        + 1.15 * features["cta"]
        + 0.55 * features["form"]
        + 0.45 * features["email"]
        + 0.65 * copy_quality
        - 0.85 * load_penalty
        - 1.10 * step_penalty
        - 2.0 * int(features["steps"] < 2)
        - 2.5 * features["broken_markup"]
    )
    return 1.0 / (1.0 + math.exp(-z))


def cohort_seed(seed: int, half: str) -> int:
    digest = hashlib.sha256(f"loop-g1-cohort-v1:{seed}:{half}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def score(root: Path, half: str, seed: int) -> dict:
    features = observed_features(root)
    probability = conversion_probability(features)
    rng = random.Random(cohort_seed(seed, half))
    conversions = sum(rng.random() < probability for _ in range(COHORT_SIZE))
    return {
        "valid": True,
        "score": round(conversions / COHORT_SIZE, 6),
        "metrics": {
            "half": half,
            "seed": seed,
            "cohort_size": COHORT_SIZE,
            "completed_signups": conversions,
            "observed_features": features,
            "canary_leak": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--half", choices=("a", "b"), required=True)
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args()
    try:
        result = score(args.candidate_dir.resolve(), args.half, args.seed)
    except Exception as exc:
        result = {"valid": False, "score": 0.0, "metrics": {"error": f"{type(exc).__name__}: {exc}"}}
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
