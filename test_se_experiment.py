#!/usr/bin/env python3
"""Tests for S1/S3 held-out oracles and the 2x2 experiment core."""

import json
import tempfile
import unittest
from pathlib import Path

import se_experiment


class OracleTest(unittest.TestCase):
    def test_s1_fix_improves_hidden_score(self):
        baseline = se_experiment.run_cell("s1", se_experiment.CELLS[0], [])
        fixed = se_experiment.run_cell("s1", se_experiment.CELLS[0], se_experiment.s1_candidates())
        self.assertGreater(fixed["final_deployed_score"], baseline["final_deployed_score"])

    def test_s3_fix_improves_operational_score(self):
        baseline = se_experiment.run_cell("s3", se_experiment.CELLS[0], [])
        fixed = se_experiment.run_cell("s3", se_experiment.CELLS[0], se_experiment.s3_candidates())
        self.assertGreater(fixed["final_deployed_score"], baseline["final_deployed_score"])


class FactorizationTest(unittest.TestCase):
    def test_grounded_gate_rejects_cosmetic_claim(self):
        result = se_experiment.run_cell("s1", se_experiment.CELLS[0], se_experiment.s1_candidates()[:1])
        self.assertFalse(result["rows"][0]["accepted"])

    def test_ungrounded_gate_accepts_cosmetic_claim(self):
        result = se_experiment.run_cell("s1", se_experiment.CELLS[2], se_experiment.s1_candidates()[:1])
        self.assertTrue(result["rows"][0]["accepted"])

    def test_feedback_factor_does_not_change_gate(self):
        numeric = se_experiment.run_cell("s1", se_experiment.CELLS[0], se_experiment.s1_candidates()[:1])
        sign = se_experiment.run_cell("s1", se_experiment.CELLS[1], se_experiment.s1_candidates()[:1])
        self.assertEqual(numeric["rows"][0]["accepted"], sign["rows"][0]["accepted"])
        self.assertNotEqual(numeric["rows"][0]["feedback"], sign["rows"][0]["feedback"])

    def test_sign_feedback_reports_oracle_sign_not_gate_acceptance(self):
        ungrounded_sign = se_experiment.CELLS[3]
        self.assertEqual(
            se_experiment.feedback_text(ungrounded_sign, accepted=True, delta=-0.1, metrics={}),
            "outcome did not improve",
        )

    def test_smoke_output_has_all_task_cell_combinations(self):
        output = se_experiment.smoke_matrix()
        combinations = {(row["task"], row["cell"]["name"]) for row in output["results"]}
        self.assertEqual(len(combinations), 8)


if __name__ == "__main__":
    unittest.main()
