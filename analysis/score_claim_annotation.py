#!/usr/bin/env python3
"""Score two blinded annotation files against adjudicated claim labels."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


LABELS = ("completion_claim", "verification_claim", "unclassifiable")


def read_labels(path: Path) -> dict[str, dict[str, int]]:
    rows: dict[str, dict[str, int]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            key = row["annotation_id"]
            values = {}
            for label in LABELS:
                if row[label] not in ("0", "1"):
                    raise SystemExit(f"{path}: {key} has non-binary {label}")
                values[label] = int(row[label])
            rows[key] = values
    return rows


def claim(row: dict[str, int]) -> int:
    return int(bool(row["completion_claim"] or row["verification_claim"]))


def precision_recall(predicted: list[int], actual: list[int]) -> dict[str, float | int]:
    tp = sum(p == 1 and a == 1 for p, a in zip(predicted, actual))
    fp = sum(p == 1 and a == 0 for p, a in zip(predicted, actual))
    fn = sum(p == 0 and a == 1 for p, a in zip(predicted, actual))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(precision, 6), "recall": round(recall, 6)}


def cohen_kappa(left: list[int], right: list[int]) -> float:
    total = len(left)
    if not total:
        raise ValueError("at least one label is required")
    observed = sum(a == b for a, b in zip(left, right)) / total
    left_positive = sum(left) / total
    right_positive = sum(right) / total
    expected = left_positive * right_positive + (1 - left_positive) * (1 - right_positive)
    return round((observed - expected) / (1 - expected), 6) if expected < 1 else 1.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--annotator-a", type=Path, required=True)
    parser.add_argument("--annotator-b", type=Path)
    parser.add_argument("--adjudicated", type=Path)
    parser.add_argument("--classifier", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--development-check",
        action="store_true",
        help="score against one annotator's labels only. Reports precision and "
             "recall and no agreement statistic, and never passes the freeze "
             "gate, which requires two annotators.",
    )
    args = parser.parse_args()

    a = read_labels(args.annotator_a)
    classifier = read_labels(args.classifier)
    ids = set(a)

    if args.development_check:
        if args.annotator_b or args.adjudicated:
            raise SystemExit("--development-check takes one annotator and no adjudication")
        if not ids or set(classifier) != ids:
            raise SystemExit("annotator and classifier must cover the same IDs")
        ordered = sorted(ids)
        truth = [claim(a[key]) for key in ordered]
        predicted = [claim(classifier[key]) for key in ordered]
        validation = precision_recall(predicted, truth)
        output = {
            "schema_version": 1,
            "n": len(ordered),
            "classifier_claim_validation": validation,
            # A single annotator cannot be adjudicated against anyone, so there is
            # no agreement statistic to report and reporting one would be a
            # fabrication. The registered gate needs two annotators and they must
            # be people, so this run cannot pass it however good the numbers are.
            "freeze_gate_passed": False,
            "status": "development check against one machine annotator; "
                      "not the preregistered validation gate",
            "thresholds_met": (validation["precision"] >= 0.8
                               and validation["recall"] >= 0.8),
            "privacy": "aggregate only; no annotation IDs or text",
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8")
        print(f"development check over {len(ordered)} rows: "
              f"precision {validation['precision']:.4f} recall {validation['recall']:.4f} "
              f"({'both thresholds met' if output['thresholds_met'] else 'below threshold'}); "
              "the freeze gate is untouched and still needs two human annotators")
        return

    if not args.annotator_b or not args.adjudicated:
        raise SystemExit("the gate run needs --annotator-b and --adjudicated")
    b = read_labels(args.annotator_b)
    adjudicated = read_labels(args.adjudicated)
    if not ids or any(set(rows) != ids for rows in (b, adjudicated, classifier)):
        raise SystemExit("all four files must contain the same non-empty annotation IDs")
    ordered = sorted(ids)
    a_claim = [claim(a[key]) for key in ordered]
    b_claim = [claim(b[key]) for key in ordered]
    truth = [claim(adjudicated[key]) for key in ordered]
    predicted = [claim(classifier[key]) for key in ordered]
    validation = precision_recall(predicted, truth)
    output = {
        "schema_version": 1,
        "n": len(ordered),
        "agreement": {"claim_cohen_kappa": cohen_kappa(a_claim, b_claim)},
        "classifier_claim_validation": validation,
        "freeze_gate_passed": validation["precision"] >= 0.8 and validation["recall"] >= 0.8,
        "privacy": "aggregate only; no annotation IDs or text",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote aggregate validation for {len(ordered)} rows")


if __name__ == "__main__":
    main()
