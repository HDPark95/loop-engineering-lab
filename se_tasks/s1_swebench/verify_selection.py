#!/usr/bin/env python3
"""Recompute the preregistered repository-scale S1 selection."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import oracle  # noqa: E402


BAND = "15 min - 1 hour"
MIN_CASES = 8
MAX_CASES = 100


def select() -> dict:
    frame_path = HERE / oracle.CONFIG["selection_frame"]
    if oracle.file_sha256(frame_path) != oracle.CONFIG["selection_frame_sha256"]:
        raise RuntimeError("screening frame digest mismatch")
    frame = json.loads(frame_path.read_text(encoding="utf-8"))
    if len(frame) != 261 or len({row["instance_id"] for row in frame}) != 261:
        raise RuntimeError("screening frame must contain 261 unique instances")

    oracle.ensure_dataset()
    rows = duckdb.sql(
        "select instance_id,repo,difficulty,FAIL_TO_PASS,PASS_TO_PASS "
        "from read_parquet(?)",
        params=[str(oracle.DATASET)],
    ).fetchall()
    published_band = {
        instance_id: {"instance_id": instance_id, "repo": repo, "difficulty": difficulty}
        for instance_id, repo, difficulty, _, _ in rows
        if difficulty == BAND
    }
    recorded_band = {row["instance_id"]: row for row in frame}
    if recorded_band != published_band:
        raise RuntimeError("screening frame is not the complete published difficulty band")

    eligible = []
    for instance_id, _, difficulty, fail_to_pass, pass_to_pass in rows:
        if instance_id not in recorded_band or difficulty != BAND:
            continue
        f2p_count = len(json.loads(fail_to_pass))
        p2p_count = len(json.loads(pass_to_pass))
        if MIN_CASES <= f2p_count <= MAX_CASES and MIN_CASES <= p2p_count <= MAX_CASES:
            eligible.append(
                {
                    "instance_id": instance_id,
                    "fail_to_pass_count": f2p_count,
                    "pass_to_pass_count": p2p_count,
                    "rank_sha256": hashlib.sha256(
                        f"{oracle.CONFIG['selection_seed']}:{instance_id}".encode()
                    ).hexdigest(),
                }
            )
    eligible.sort(key=lambda row: row["rank_sha256"])
    if not eligible:
        raise RuntimeError("selection rule produced no eligible instances")
    return {
        "screening_frame_count": len(frame),
        "eligible_count": len(eligible),
        "selected": eligible[0],
        "eligible_in_rank_order": eligible,
    }


def main() -> int:
    result = select()
    if result["selected"]["instance_id"] != oracle.CONFIG["instance_id"]:
        raise RuntimeError("registered S1 instance does not match the selection rule")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"selection verification failed: {exc}")
        raise SystemExit(1)
