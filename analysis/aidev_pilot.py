#!/usr/bin/env python3
"""Run the preregistration-gated AIDev feasibility pilot.

The script reads the pinned AIDev parquet tables, selects a deterministic
10,000-PR sample, and writes aggregate-only JSON/CSV outputs. It deliberately
does not copy PR text, user names, URLs, or repository identifiers into the
replication package.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

import duckdb


DATASET_REPOSITORY = "hao-li/AIDev"
DATASET_REVISION = "68ed5f4b80d27a9e057fc57567f38bd322ac73ec"
DEFAULT_SAMPLE_SEED = "loop-engineering-aidev-pilot-v1"
REQUIRED_TABLES = (
    "pull_request.parquet",
    "pr_reviews.parquet",
    "pr_timeline.parquet",
    "pr_commits.parquet",
    "pr_task_type.parquet",
    "human_pull_request.parquet",
    "human_pr_task_type.parquet",
)

# Claim classifier v2 (2026-08-10). v1 was a conservative lexical rule written for
# a feasibility check, and the preregistered validation gate measured it at
# precision 0.913 and recall 0.640 against adjudicated labels. The 0.80 recall
# threshold failed, so registration 3.2 step 4 applies and the rule is replaced.
#
# The v1 miss was structural, not a matter of missing vocabulary: every completion
# pattern required a determiner immediately after the assertion verb
# ("implemented the", "fixed this"), so "I implemented Turborepo support" and
# "Added a hot reload feature" did not match. Of the 89 misses, 78 contained a
# past-tense or perfect assertion of completed work, and the two independent
# annotators agreed with each other on 84 of them, so the misses were the rule's
# fault rather than label noise.
#
# v2 matches the assertion form and leaves the object free. The construct's own
# discriminator is carried over verbatim: "adds a retry helper" is a description,
# "the retry problem is now solved" is a claim.
#
# Deliberately NOT included: `(was|were) <verb>ed`. "The version field was updated
# from 2.1.1 to 2.1.2" describes what the diff contains rather than asserting that
# the work is finished, and it was the lowest-precision candidate pattern measured
# (0.762). Also not included: a bare past-tense bullet change list, which is the one
# construction on which the two annotators most often disagreed.
_ASSERTION_VERB = (
    r"(?:implement|add|fix|resolve|complete|finish|deliver|restore|remove|update|"
    r"upgrade|create|bump|migrate|refactor|rename|delete|introduce|correct|"
    r"replace|expose|enable|support|handle|address|port|convert|extract|split)"
)

COMPLETION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    for pattern in (
        # first person, past tense: "I implemented X", "we have added Y"
        rf"\b(?:I|we)\s+(?:have\s+)?{_ASSERTION_VERB}(?:ed|d)\b",
        # the change as subject: "this PR fixes X", "these changes implement Y"
        rf"\b(?:this\s+(?:pull\s+request|pr|change|commit)|these\s+changes)\s+"
        rf"(?:has\s+|have\s+)?{_ASSERTION_VERB}(?:ed|d|s)\b",
        # sentence-initial past tense: "Added a hot reload feature"
        rf"^\s*(?:[-*+]\s+|\d+\.\s+)?{_ASSERTION_VERB}(?:ed|d)\b",
        # passive perfect: "the endpoint has been exposed"
        rf"\b(?:has|have|had)\s+been\s+{_ASSERTION_VERB}(?:ed|d)\b",
        # explicit statement of a finished state
        r"\b(?:implementation|work|task|feature|fix|change|migration|refactor)\s+"
        r"(?:is|are|has been|have been)\s+(?:now\s+)?"
        r"(?:complete|completed|done|finished|ready)\b",
        # "the retry problem is now solved" is the construct's own example of a
        # claim, against "adds a retry helper" as its example of a description.
        r"\b(?:is|are)\s+now\s+"
        r"(?:working|fixed|resolved|solved|addressed|handled|supported|available"
        r"|complete|done)\b",
        r"\bready\s+for\s+(?:review|merge|merging|testing|production)\b",
        r"\b(?:successfully|fully)\s+\w+ed\b",
        # GitHub issue-closing keywords. A digit is required so that the unfilled
        # template placeholder "Fixes # (issue)" does not count as an assertion.
        r"\b(?:fixes|closes|resolves|fixed|closed|resolved)\s+#\d+\b",
        # CJK completion assertions; the field layer reports non-English bodies as a
        # prespecified subgroup and cannot do that if it cannot code them at all.
        r"(?:\u4fee\u590d\u4e86|\u5b9e\u73b0\u4e86|\u5b8c\u6210\u4e86|\u6dfb\u52a0\u4e86|\u5df2\u5b8c\u6210|\u5df2\u4fee\u590d|\u5df2\u5b9e\u73b0)",
        r"(?:\u4fee\u6b63\u3057\u307e\u3057\u305f|\u5b9f\u88c5\u3057\u307e\u3057\u305f|\u5b8c\u4e86\u3057\u307e\u3057\u305f|\u5bfe\u5fdc\u3057\u307e\u3057\u305f)",
        r"(?:\uc218\uc815\ud588|\uad6c\ud604\ud588|\uc644\ub8cc\ud588|\ucd94\uac00\ud588)",
    )
)
VERIFICATION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    for pattern in (
        r"\b(?:all\s+)?tests?\s+(?:pass|passed|passing|succeed|succeeded|are\s+green)\b",
        r"\b(?:tested|verified|validated|confirmed)\s+"
        r"(?:the|this|that|all|it|locally|manually|successfully|in|on|with|by|and)\b",
        r"\b(?:ci|checks?|build|pipeline|lint|suite)\s+(?:is\s+|are\s+)?"
        r"(?:green|pass|passes|passed|passing|successful|clean)\b",
        r"\b(?:no|zero)\s+(?:new\s+)?(?:test\s+)?(?:failures?|regressions?|errors?)\b",
        r"\bverified\s+(?:by|via|through)\b",
        r"\b(?:I|we)\s+(?:have\s+)?(?:tested|verified|validated|confirmed|ran|run)\b",
        r"(?:\u6d4b\u8bd5\u901a\u8fc7|\u5df2\u9a8c\u8bc1|\u9a8c\u8bc1\u901a\u8fc7)",
    )
)
STRONG_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:successfully|fully)\s+(?:implemented|completed|fixed|resolved|verified|validated)\b",
        r"\ball\s+(?:tests?|checks?)\s+(?:pass|passed|passing)\b",
        r"\b(?:implementation|work|task|feature|fix)\s+(?:is|has been)\s+"
        r"(?:complete|completed|done|finished)\b",
    )
)
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
REVERT_PATTERN = re.compile(r"\brevert(?:ed|s|ing)?\b", re.IGNORECASE)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_data_dir(data_dir: Path) -> dict[str, str]:
    missing = [name for name in REQUIRED_TABLES if not (data_dir / name).is_file()]
    if missing:
        raise SystemExit("missing AIDev tables: " + ", ".join(missing))
    return {name: sha256_file(data_dir / name) for name in REQUIRED_TABLES}


def matches_any(patterns: Iterable[re.Pattern[str]], text: str) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def classify_claim(body: str | None) -> dict[str, bool | str]:
    # Template boilerplate lives in HTML comments and is instruction to the author,
    # not an assertion by the author, so it is removed before matching.
    text = HTML_COMMENT.sub(" ", body or "")
    completion = matches_any(COMPLETION_PATTERNS, text)
    verification = matches_any(VERIFICATION_PATTERNS, text)
    strong = matches_any(STRONG_PATTERNS, text)
    if strong:
        strength = "strong"
    elif verification:
        strength = "verification"
    elif completion:
        strength = "completion"
    else:
        strength = "none"
    return {
        "claim": completion or verification,
        "completion_claim": completion,
        "verification_claim": verification,
        "strong_claim": strong,
        "strength": strength,
    }


def deterministic_sample(rows: list[tuple], sample_size: int, seed: str) -> list[tuple]:
    if sample_size > len(rows):
        raise SystemExit(f"sample size {sample_size} exceeds AI PR population {len(rows)}")

    def sample_key(row: tuple) -> bytes:
        return hashlib.sha256(f"{seed}:{row[0]}".encode("ascii")).digest()

    return sorted(rows, key=sample_key)[:sample_size]


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> list[float | None]:
    if total == 0:
        return [None, None]
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    margin = z * math.sqrt((proportion * (1 - proportion) + z * z / (4 * total)) / total) / denominator
    return [round(max(0.0, centre - margin), 6), round(min(1.0, centre + margin), 6)]


def rate(successes: int, total: int) -> dict[str, int | float | list[float | None] | None]:
    return {
        "numerator": successes,
        "denominator": total,
        "rate": round(successes / total, 6) if total else None,
        "wilson_95": wilson_interval(successes, total),
    }


def aggregate_group(records: list[dict]) -> dict:
    total = len(records)
    resolved = [record for record in records if record["merged"] or record["closed_unmerged"]]
    claimed = [record for record in records if record["claim"]]
    unclaimed = [record for record in records if not record["claim"]]
    return {
        "n": total,
        "body_present": rate(sum(record["body_present"] for record in records), total),
        "completion_claim": rate(sum(record["completion_claim"] for record in records), total),
        "verification_claim": rate(sum(record["verification_claim"] for record in records), total),
        "strong_claim": rate(sum(record["strong_claim"] for record in records), total),
        "any_claim": rate(len(claimed), total),
        "merged": rate(sum(record["merged"] for record in records), total),
        "closed_unmerged": rate(sum(record["closed_unmerged"] for record in records), total),
        "resolved_merge_rate": rate(sum(record["merged"] for record in resolved), len(resolved)),
        "merge_rate_given_claim": rate(sum(record["merged"] for record in claimed), len(claimed)),
        "merge_rate_without_claim": rate(sum(record["merged"] for record in unclaimed), len(unclaimed)),
    }


def fetch_rows(connection: duckdb.DuckDBPyConnection, path: Path, columns: str) -> list[tuple]:
    return connection.execute(
        f"SELECT {columns} FROM read_parquet(?)", [str(path)]
    ).fetchall()


def build_records(rows: list[tuple]) -> list[dict]:
    records = []
    for pr_id, body, agent, state, merged_at in rows:
        claim = classify_claim(body)
        merged = merged_at is not None
        records.append(
            {
                "id": pr_id,
                "agent": agent or "unknown",
                "body_present": bool(body and body.strip()),
                "merged": merged,
                "closed_unmerged": str(state or "").lower() == "closed" and not merged,
                **claim,
            }
        )
    return records


def rows_by_key(records: list[dict], key: str) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        groups[str(record.get(key) or "unknown")].append(record)
    return groups


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--sample-size", type=int, default=10_000)
    parser.add_argument("--sample-seed", default=DEFAULT_SAMPLE_SEED)
    args = parser.parse_args()

    checksums = validate_data_dir(args.data_dir)
    connection = duckdb.connect()
    ai_population = fetch_rows(
        connection,
        args.data_dir / "pull_request.parquet",
        "id, body, agent, state, merged_at",
    )
    sampled_rows = deterministic_sample(ai_population, args.sample_size, args.sample_seed)
    ai_records = build_records(sampled_rows)
    sample_ids = {record["id"] for record in ai_records}

    task_types = dict(
        fetch_rows(connection, args.data_dir / "pr_task_type.parquet", "id, type")
    )
    for record in ai_records:
        record["task_type"] = task_types.get(record["id"], "unknown") or "unknown"

    reviews = fetch_rows(connection, args.data_dir / "pr_reviews.parquet", "pr_id, state")
    reviewed_ids = {pr_id for pr_id, _ in reviews if pr_id in sample_ids}
    changes_requested_ids = {
        pr_id for pr_id, state in reviews
        if pr_id in sample_ids and str(state or "").upper() == "CHANGES_REQUESTED"
    }

    commits = fetch_rows(connection, args.data_dir / "pr_commits.parquet", "pr_id, message")
    revert_marker_ids = {
        pr_id for pr_id, message in commits
        if pr_id in sample_ids and REVERT_PATTERN.search(message or "")
    }
    timeline = fetch_rows(
        connection,
        args.data_dir / "pr_timeline.parquet",
        "pr_id, event, created_at, message",
    )
    timeline_revert_ids = {
        pr_id for pr_id, _event, _created_at, message in timeline
        if pr_id in sample_ids and REVERT_PATTERN.search(message or "")
    }
    committed_events = [
        created_at for pr_id, event, created_at, _message in timeline
        if pr_id in sample_ids and event == "committed"
    ]
    committed_event_timestamp_coverage = sum(value is not None for value in committed_events)

    human_rows = fetch_rows(
        connection,
        args.data_dir / "human_pull_request.parquet",
        "id, body, agent, state, merged_at",
    )
    human_records = build_records(human_rows)

    per_agent = {
        key: aggregate_group(records)
        for key, records in sorted(rows_by_key(ai_records, "agent").items())
    }
    per_task_type = {
        key: aggregate_group(records)
        for key, records in sorted(rows_by_key(ai_records, "task_type").items())
    }
    overall = aggregate_group(ai_records)
    claimed = overall["merge_rate_given_claim"]["rate"]
    unclaimed = overall["merge_rate_without_claim"]["rate"]
    merge_rate_difference = round(claimed - unclaimed, 6) if claimed is not None and unclaimed is not None else None

    output = {
        "schema_version": 1,
        "dataset": {
            "repository": DATASET_REPOSITORY,
            "revision": DATASET_REVISION,
            "license": "CC-BY-4.0",
            "table_sha256": checksums,
        },
        "sampling": {
            "population": len(ai_population),
            "sample_size": len(ai_records),
            "seed": args.sample_seed,
            "method": "lowest SHA-256(seed + ':' + PR id), without replacement",
        },
        "claim_coding": {
            "status": "lexical feasibility rule; construct validation required before confirmatory use",
            "ai_sample": overall,
            "human_baseline_all_available": aggregate_group(human_records),
            "merge_rate_difference_claim_minus_no_claim": merge_rate_difference,
            "per_agent": per_agent,
            "per_task_type": per_task_type,
        },
        "outcome_observability": {
            "merge_status": {"available": True, "sample_merged": sum(r["merged"] for r in ai_records)},
            "closed_unmerged": {
                "available": True,
                "sample_closed_unmerged": sum(r["closed_unmerged"] for r in ai_records),
            },
            "reviews": {
                "available": True,
                "sample_with_review": len(reviewed_ids),
                "sample_with_changes_requested": len(changes_requested_ids),
            },
            "within_pr_revert_marker": {
                "available": True,
                "sample_prs": len(revert_marker_ids | timeline_revert_ids),
                "interpretation": "lexical marker inside the same PR, not a post-merge revert",
            },
            "post_merge_revert": {
                "available": False,
                "reason": "committed timeline events lack timestamps and the supplied tables do not link later revert PRs to the merged PR",
                "committed_events_in_sample": len(committed_events),
                "committed_events_with_timestamp": committed_event_timestamp_coverage,
            },
        },
        "p3_decision": {
            "completion_claim_extractable": overall["any_claim"]["numerator"] > 0,
            "merge_and_change_request_outcomes_extractable": True,
            "post_merge_revert_extractable": False,
            "human_baseline_comparable": len(human_records) > 0,
            "design_action": "Use merge/closed-unmerged and changes-requested as field outcomes; exclude post-merge revert until a separately timestamped cross-PR linkage is mined.",
        },
        "privacy": {
            "aggregate_only": True,
            "raw_text_written": False,
            "user_or_repository_identifiers_written": False,
        },
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "aidev_pilot_summary.json"
    json_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    csv_path = args.output_dir / "aidev_pilot_by_agent.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(("agent", "n", "claim_n", "claim_rate", "merged_n", "merge_rate"))
        for agent, aggregate in per_agent.items():
            writer.writerow(
                (
                    agent,
                    aggregate["n"],
                    aggregate["any_claim"]["numerator"],
                    aggregate["any_claim"]["rate"],
                    aggregate["merged"]["numerator"],
                    aggregate["merged"]["rate"],
                )
            )

    print(json.dumps({
        "json": str(json_path),
        "csv": str(csv_path),
        "sample_size": len(ai_records),
        "claim_rate": overall["any_claim"]["rate"],
        "merged_rate": overall["merged"]["rate"],
        "post_merge_revert_extractable": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
