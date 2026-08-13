#!/usr/bin/env python3
"""Prepare a private, blinded 400-PR claim-annotation packet.

Outputs contain raw PR bodies and must stay under the gitignored data directory.
Only aggregate validation counts produced by score_claim_annotation.py belong
in the public replication package.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
from collections import defaultdict
from pathlib import Path

import duckdb

from aidev_pilot import DATASET_REVISION, DEFAULT_SAMPLE_SEED, classify_claim, deterministic_sample


ANNOTATION_SEED = "loop-engineering-claim-annotation-v1"


def annotation_key(pr_id: int | str) -> str:
    return hashlib.sha256(f"{ANNOTATION_SEED}:{pr_id}".encode("ascii")).hexdigest()[:20]


def stratified_rows(
    rows: list[tuple], sample_size: int, exclude: frozenset[str] = frozenset()
) -> list[tuple]:
    """Draw a stratified packet, optionally disjoint from an earlier one.

    Registration 3.2 step 4 requires that a replaced classifier be validated on a
    NEW 400-PR subset, so a second packet must not reuse the first one's rows. The
    within-stratum ranking is a fixed hash of the annotation seed, so passing the
    first packet's annotation ids as `exclude` simply advances to the next rows in
    the same ranking. No new randomness is introduced, and nothing about which rows
    are drawn depends on any label or outcome.
    """
    groups: dict[tuple[str, bool], list[tuple]] = defaultdict(list)
    for row in rows:
        pr_id, body, agent = row
        if annotation_key(pr_id) in exclude:
            continue
        groups[(agent or "unknown", bool(classify_claim(body)["claim"]))].append(row)
    ranked_groups = {}
    for stratum, candidates in groups.items():
        ranked_groups[stratum] = sorted(
            candidates,
            key=lambda row: hashlib.sha256(
                f"{ANNOTATION_SEED}:{stratum}:{row[0]}".encode("ascii")
            ).digest(),
        )
    if sum(map(len, ranked_groups.values())) < sample_size:
        raise SystemExit("annotation sample exceeds the pilot population")
    quota = sample_size // len(ranked_groups)
    counts = {key: min(quota, len(values)) for key, values in ranked_groups.items()}
    remaining = sample_size - sum(counts.values())
    for key in sorted(ranked_groups, key=lambda item: (-len(ranked_groups[item]), item)):
        extra = min(remaining, len(ranked_groups[key]) - counts[key])
        counts[key] += extra
        remaining -= extra
        if not remaining:
            break
    selected = [row for key, values in ranked_groups.items() for row in values[: counts[key]]]
    return sorted(selected, key=lambda row: annotation_key(row[0]))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--pilot-size", type=int, default=10_000)
    parser.add_argument("--sample-size", type=int, default=400)
    parser.add_argument(
        "--exclude-packet",
        type=Path,
        action="append",
        default=[],
        help="an earlier packet CSV whose rows must not be drawn again; repeatable",
    )
    args = parser.parse_args()

    exclude: set[str] = set()
    for earlier in args.exclude_packet:
        with earlier.open(newline="", encoding="utf-8") as handle:
            exclude.update(row["annotation_id"] for row in csv.DictReader(handle))

    safe_root = (Path.cwd() / "data").resolve()
    if not args.output_dir.resolve().is_relative_to(safe_root):
        raise SystemExit("annotation output must stay under the gitignored data/ directory")

    table = args.data_dir / "pull_request.parquet"
    rows = duckdb.connect().execute(
        "SELECT id, body, agent FROM read_parquet(?)", [str(table)]
    ).fetchall()
    pilot = deterministic_sample(rows, args.pilot_size, DEFAULT_SAMPLE_SEED)
    selected = stratified_rows(pilot, args.sample_size, frozenset(exclude))
    args.output_dir.mkdir(parents=True, exist_ok=True)

    packet = args.output_dir / "claim_annotation_packet.csv"
    manifest = args.output_dir / "claim_annotation_manifest.csv"
    classifier = args.output_dir / "claim_annotation_classifier.csv"
    with packet.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("annotation_id", "body", "completion_claim", "verification_claim", "unclassifiable"),
            lineterminator="\n",
        )
        writer.writeheader()
        for pr_id, body, _agent in selected:
            writer.writerow({"annotation_id": annotation_key(pr_id), "body": body or ""})

    with manifest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("annotation_id", "agent", "preliminary_claim", "dataset_revision"),
            lineterminator="\n",
        )
        writer.writeheader()
        for pr_id, body, agent in selected:
            writer.writerow(
                {
                    "annotation_id": annotation_key(pr_id),
                    "agent": agent or "unknown",
                    "preliminary_claim": int(bool(classify_claim(body)["claim"])),
                    "dataset_revision": DATASET_REVISION,
                }
            )

    with classifier.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("annotation_id", "completion_claim", "verification_claim", "unclassifiable"),
            lineterminator="\n",
        )
        writer.writeheader()
        for pr_id, body, _agent in selected:
            labels = classify_claim(body)
            writer.writerow(
                {
                    "annotation_id": annotation_key(pr_id),
                    "completion_claim": int(bool(labels["completion_claim"])),
                    "verification_claim": int(bool(labels["verification_claim"])),
                    "unclassifiable": 0,
                }
            )
    overlap = exclude & {annotation_key(pr_id) for pr_id, _b, _a in selected}
    if overlap:
        raise SystemExit(f"packet overlaps an excluded packet on {len(overlap)} rows")
    print(f"wrote {len(selected)} private annotation rows"
          + (f", disjoint from {len(exclude)} excluded rows" if exclude else "")
          + "; do not commit the CSV files")


if __name__ == "__main__":
    main()
