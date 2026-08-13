#!/usr/bin/env python3
"""Tests for annotation selection and aggregate scoring."""

import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


sys.path.insert(0, str(pathlib.Path(__file__).parent))


def load(name: str):
    path = pathlib.Path(__file__).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


PREPARE = load("prepare_claim_annotation")
SCORE = load("score_claim_annotation")


class AnnotationTest(unittest.TestCase):
    def test_annotation_key_is_stable_and_opaque(self):
        self.assertEqual(PREPARE.annotation_key(123), PREPARE.annotation_key(123))
        self.assertNotIn("123", PREPARE.annotation_key(123))

    def test_stratification_redistributes_a_short_stratum(self):
        rows = [
            (1, "All tests passed", "a"),
            (2, "Fix parser", "a"),
            (3, "Fix parser", "a"),
            (4, "All tests passed", "b"),
            (5, "All tests passed", "b"),
            (6, "Fix parser", "b"),
            (7, "Fix parser", "b"),
        ]
        self.assertEqual(len(PREPARE.stratified_rows(rows, 7)), 7)

    def test_precision_recall(self):
        result = SCORE.precision_recall([1, 1, 0, 0], [1, 0, 1, 0])
        self.assertEqual((result["precision"], result["recall"]), (0.5, 0.5))

    def test_perfect_kappa(self):
        self.assertEqual(SCORE.cohen_kappa([1, 0, 1], [1, 0, 1]), 1.0)

    def test_perfect_machine_labels_cannot_pass_confirmatory_freeze(self):
        csv_text = (
            "annotation_id,completion_claim,verification_claim,unclassifiable\n"
            "opaque-a,1,0,0\n"
            "opaque-b,0,1,0\n"
        )
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            inputs = []
            for name in ("a.csv", "b.csv", "adjudicated.csv", "classifier.csv"):
                path = root / name
                path.write_text(csv_text, encoding="utf-8")
                inputs.append(path)
            output = root / "result.json"
            argv = [
                "score_claim_annotation.py",
                "--annotator-a", str(inputs[0]),
                "--annotator-b", str(inputs[1]),
                "--adjudicated", str(inputs[2]),
                "--classifier", str(inputs[3]),
                "--output", str(output),
            ]
            with mock.patch.object(sys, "argv", argv):
                SCORE.main()
            result = json.loads(output.read_text(encoding="utf-8"))
        self.assertTrue(result["instrument_thresholds_met"])
        self.assertFalse(result["freeze_gate_passed"])


if __name__ == "__main__":
    unittest.main()
