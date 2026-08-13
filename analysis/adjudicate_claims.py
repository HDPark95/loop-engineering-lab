#!/usr/bin/env python3
"""Prepare disputed annotation rows and deterministically merge third ratings.

This is an exploratory, private-data helper. Initial A/B agreements enter the
adjudicated set directly. Initial disagreements enter a blinded dispute packet;
they enter the final set only when the independent Claude and Codex third
ratings agree exactly. No label is inferred or majority-voted by this script.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


LABEL_FIELDS = ("completion_claim", "verification_claim", "unclassifiable")
OUTPUT_FIELDS = ("annotation_id", *LABEL_FIELDS)


def read_rows(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = {row["annotation_id"]: row for row in csv.DictReader(handle)}
    if not rows:
        raise ValueError(f"{path} contains no annotation rows")
    return rows


def labels(row: dict[str, str]) -> tuple[int, int, int]:
    values = tuple(int(row[field]) for field in LABEL_FIELDS)
    if any(value not in (0, 1) for value in values):
        raise ValueError(f"{row.get('annotation_id')}: labels must be binary")
    if values[2] and values[:2] != (0, 0):
        raise ValueError(
            f"{row.get('annotation_id')}: unclassifiable excludes claim labels"
        )
    return values


def write_labels(path: Path, rows: list[dict[str, int | str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def adjudicate(
    packet_path: Path,
    annotator_a_path: Path,
    annotator_b_path: Path,
    disputes_output: Path,
    third_claude_path: Path | None = None,
    third_codex_path: Path | None = None,
    adjudicated_output: Path | None = None,
) -> tuple[int, int]:
    packet = read_rows(packet_path)
    annotator_a = read_rows(annotator_a_path)
    annotator_b = read_rows(annotator_b_path)
    ids = set(packet)
    if set(annotator_a) != ids or set(annotator_b) != ids:
        raise ValueError("packet and initial annotators must cover the same IDs")

    agreed: list[dict[str, int | str]] = []
    disputed_ids = []
    for key in sorted(ids):
        left = labels(annotator_a[key])
        right = labels(annotator_b[key])
        if left == right:
            agreed.append(dict(zip(OUTPUT_FIELDS, (key, *left), strict=True)))
        else:
            disputed_ids.append(key)

    disputes_output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(next(iter(packet.values())))
    with disputes_output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(packet[key] for key in disputed_ids)

    third_inputs = (third_claude_path, third_codex_path, adjudicated_output)
    if any(value is not None for value in third_inputs) and not all(
        value is not None for value in third_inputs
    ):
        raise ValueError(
            "third Claude, third Codex, and adjudicated output must be supplied together"
        )
    unresolved = len(disputed_ids)
    if all(value is not None for value in third_inputs):
        assert third_claude_path is not None
        assert third_codex_path is not None
        assert adjudicated_output is not None
        third_claude = read_rows(third_claude_path)
        third_codex = read_rows(third_codex_path)
        wanted = set(disputed_ids)
        if set(third_claude) != wanted or set(third_codex) != wanted:
            raise ValueError("both third raters must cover exactly the disputed IDs")
        unresolved = 0
        for key in disputed_ids:
            left = labels(third_claude[key])
            right = labels(third_codex[key])
            if left == right:
                agreed.append(dict(zip(OUTPUT_FIELDS, (key, *left), strict=True)))
            else:
                unresolved += 1
        write_labels(adjudicated_output, sorted(agreed, key=lambda row: row["annotation_id"]))
    return len(disputed_ids), unresolved


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--annotator-a", type=Path, required=True)
    parser.add_argument("--annotator-b", type=Path, required=True)
    parser.add_argument("--disputes-output", type=Path, required=True)
    parser.add_argument("--third-claude", type=Path)
    parser.add_argument("--third-codex", type=Path)
    parser.add_argument("--adjudicated-output", type=Path)
    args = parser.parse_args()
    try:
        disputes, unresolved = adjudicate(
            args.packet,
            args.annotator_a,
            args.annotator_b,
            args.disputes_output,
            args.third_claude,
            args.third_codex,
            args.adjudicated_output,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f"disputes={disputes} unresolved_after_third_ratings={unresolved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
