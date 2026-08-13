#!/usr/bin/env python3
"""Tests for the frozen trajectory/block analysis contract."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


PATH = Path(__file__).with_name("fit_clustered.py")
SPEC = importlib.util.spec_from_file_location("fit_clustered", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def rows(cell: str = "grounded-numeric", accepted=(False, False)) -> list[dict]:
    grounded = cell.startswith("grounded")
    return [
        {
            "trajectory": f"s1|codex|model|{cell}|1",
            "task": "s1",
            "agent": "codex",
            "seed": 1,
            "cell": cell,
            "cell_gate_grounded": grounded,
            "cell_feedback": "numeric" if cell.endswith("numeric") else "sign",
            "cycle": cycle,
            "cycles_planned": 2,
            "delta_hob": delta,
            "accepted": decision,
            "baseline_score_hob": 0.1,
            "oracle_score_hob": 0.2 if cycle == 1 else 0.3,
            "deployed_score_hob": 0.2 if cycle == 1 else 0.3,
            "apparatus_test": False,
            "confirmatory_eligible": True,
        }
        for cycle, (delta, decision) in enumerate(zip((-0.1, 0.1), accepted), start=1)
    ]


class TrajectoryReductionTest(unittest.TestCase):
    def test_incidence_has_a_fixed_cycle_denominator(self):
        record = MODULE.trajectory_record(rows(accepted=(False, False)))
        self.assertEqual(record["harmful_acceptance_incidence"], 0.0)
        self.assertEqual(record["false_rejection_incidence"], 0.5)

    def test_missing_hob_refuses_confirmatory_analysis(self):
        data = rows()
        del data[1]["delta_hob"]
        with self.assertRaisesRegex(ValueError, "delta_hob"):
            MODULE.trajectory_record(data)

    def test_apparatus_rows_are_refused(self):
        data = rows()
        data[0]["apparatus_test"] = True
        with self.assertRaisesRegex(ValueError, "apparatus"):
            MODULE.trajectory_record(data)


class BlockInferenceTest(unittest.TestCase):
    def test_all_four_cells_are_required(self):
        records = [MODULE.trajectory_record(rows(cell)) for cell in sorted(MODULE.EXPECTED_CELLS)]
        self.assertEqual(len(MODULE.make_blocks(records)), 1)
        with self.assertRaisesRegex(ValueError, "all four"):
            MODULE.make_blocks(records[:-1])

    def test_exact_sign_flip_is_one_sided(self):
        self.assertEqual(MODULE.exact_sign_flip_p([1.0, 1.0, 1.0]), 0.125)
        self.assertEqual(MODULE.exact_sign_flip_p([-1.0, -1.0, -1.0]), 1.0)

    def test_holm_accepts_only_the_registered_family(self):
        verdicts = MODULE.holm({"B-H1a": 0.01, "B-H1b": 0.04})
        self.assertTrue(verdicts["B-H1a"]["reject_null"])
        self.assertTrue(verdicts["B-H1b"]["reject_null"])
        with self.assertRaisesRegex(ValueError, "exactly"):
            MODULE.holm({"B-H1a": 0.01})


if __name__ == "__main__":
    unittest.main(verbosity=2)
