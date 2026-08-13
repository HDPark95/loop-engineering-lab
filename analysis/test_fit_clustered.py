#!/usr/bin/env python3
"""Tests for the frozen trajectory/block analysis contract."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PATH = Path(__file__).with_name("fit_clustered.py")
SPEC = importlib.util.spec_from_file_location("fit_clustered", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def rows(cell: str = "grounded-numeric", accepted=(False, False)) -> list[dict]:
    grounded = cell.startswith("grounded")
    position = sorted(MODULE.EXPECTED_CELLS).index(cell) + 1
    return [
        {
            "trajectory": f"s1|codex|model|{cell}|1",
            "task": "s1_swebench",
            "agent": "codex",
            "seed": 1,
            "cell": cell,
            "cell_schedule_seed": "analysis-test-order-v1",
            "cell_schedule_position": position,
            "cell_gate_grounded": grounded,
            "cell_feedback": "numeric" if cell.endswith("numeric") else "sign",
            "cycle": cycle,
            "cycles_planned": 2,
            "delta_hob": delta,
            "delta_hoa": 0.1 if cycle == 1 else -0.1,
            "accepted": decision,
            "baseline_score_hob": 0.1,
            "oracle_score_hob": 0.2 if cycle == 1 else 0.3,
            "deployed_score_hob": 0.2 if cycle == 1 else 0.3,
            "apparatus_test": False,
            "confirmatory_eligible": True,
            "candidate_changed": cycle == 1,
            "agent_completed": True,
            "edit_success": cycle == 1,
            "input_tokens": 100,
            "output_tokens": 10,
            "agent_seconds": 1.0,
            "judge_seconds": 0.0,
            "oracle_seconds": 0.5,
            "api_equivalent_usd_lower_bound": 0.008,
            "api_equivalent_usd": 0.01,
            "incremental_billed_usd": 0.0,
        }
        for cycle, (delta, decision) in enumerate(
            zip((-0.1, 0.1), accepted, strict=True), start=1
        )
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

    def test_registered_secondary_and_cost_fields_are_reduced(self):
        record = MODULE.trajectory_record(rows(accepted=(True, False)))
        self.assertEqual(record["cycles_to_first_positive_hob"], 2)
        self.assertFalse(record["cycles_to_first_positive_hob_censored"])
        self.assertEqual(record["first_positive_censor_incidence"], 0.0)
        self.assertEqual(record["erosion_hob"], 0.0)
        self.assertEqual(record["gate_mirage_rate"], 0.0)
        self.assertEqual(record["edit_success_rate"], 0.5)
        self.assertEqual(record["input_tokens"], 200.0)
        self.assertEqual(record["wall_clock_seconds"], 3.0)
        self.assertIsNone(record["gain_per_incremental_billed_usd"])

    def test_missing_edit_success_contract_is_refused(self):
        data = rows()
        del data[0]["edit_success"]
        with self.assertRaisesRegex(ValueError, "edit-success"):
            MODULE.trajectory_record(data)

    def test_ineligible_and_incomplete_trajectories_are_refused(self):
        ineligible = rows()
        ineligible[1]["confirmatory_eligible"] = False
        with self.assertRaisesRegex(ValueError, "confirmatory_eligible"):
            MODULE.trajectory_record(ineligible)

        with self.assertRaisesRegex(ValueError, "incomplete"):
            MODULE.trajectory_record(rows()[:1])

    def test_cell_schedule_assignment_is_required_and_constant(self):
        data = rows()
        data[1]["cell_schedule_position"] = 2
        with self.assertRaisesRegex(ValueError, "cell-schedule"):
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
        with self.assertRaisesRegex(ValueError, "limited to 20"):
            MODULE.exact_sign_flip_p([1.0] * 21)

    def test_block_structure_and_shared_cycle_one_are_fail_closed(self):
        records = [
            MODULE.trajectory_record(rows(cell))
            for cell in sorted(MODULE.EXPECTED_CELLS)
        ]
        records[0]["cycle1_hob_score"] = 0.9
        with self.assertRaisesRegex(ValueError, "share one cycle-one"):
            MODULE.make_blocks(records)

        records[0]["cycle1_hob_score"] = 0.2
        records[0]["grounded"] = False
        with self.assertRaisesRegex(ValueError, "two grounded"):
            MODULE.block_difference(records, "delivered_hob_gain")

        records[0]["grounded"] = records[0]["cell"].startswith("grounded")
        records[0]["cell_schedule_position"] = 2
        with self.assertRaisesRegex(ValueError, "cell-schedule positions"):
            MODULE.make_blocks(records)

    def test_holm_accepts_only_the_registered_family(self):
        verdicts = MODULE.holm({"B-H1a": 0.01, "B-H1b": 0.04})
        self.assertTrue(verdicts["B-H1a"]["reject_null"])
        self.assertTrue(verdicts["B-H1b"]["reject_null"])
        with self.assertRaisesRegex(ValueError, "exactly"):
            MODULE.holm({"B-H1a": 0.01})

    def test_full_grid_emits_primary_secondary_and_cost_outputs(self):
        harmful_cycles = {
            "s1_swebench": 5,
            "s3": 5,
            "g1": 4,
            "b1": 2,
        }
        records = []
        for task, harmful_count in harmful_cycles.items():
            for agent in ("codex", "claude"):
                for seed in (11, 23, 37, 53, 71):
                    for cell in sorted(MODULE.EXPECTED_CELLS):
                        grounded = cell.startswith("grounded")
                        trajectory = f"{task}|{agent}|model|{cell}|{seed}"
                        for cycle in range(1, 7):
                            accepted = cycle == 1 or (
                                not grounded and cycle <= harmful_count + 1
                            )
                            delta = 0.1 if cycle == 1 else 0.0
                            records.append(
                                {
                                    "schema_version": 2,
                                    "trajectory": trajectory,
                                    "attempt_id": "complete",
                                    "task": task,
                                    "agent": agent,
                                    "seed": seed,
                                    "cell": cell,
                                    "cell_schedule_seed": "full-grid-test-order-v1",
                                    "cell_schedule_position": (
                                        sorted(MODULE.EXPECTED_CELLS).index(cell) + 1
                                    ),
                                    "cell_gate_grounded": grounded,
                                    "cell_feedback": (
                                        "numeric" if cell.endswith("numeric") else "sign"
                                    ),
                                    "cycle": cycle,
                                    "cycles_planned": 6,
                                    "delta_hoa": delta,
                                    "delta_hob": delta,
                                    "oracle_delta": delta,
                                    "accepted": accepted,
                                    "baseline_score_hob": 0.1,
                                    "oracle_score_hob": 0.2,
                                    "deployed_score_hob": 0.2,
                                    "apparatus_test": False,
                                    "confirmatory_eligible": True,
                                    "candidate_changed": cycle == 1,
                                    "agent_completed": True,
                                    "edit_success": cycle == 1,
                                    "input_tokens": 100.0,
                                    "output_tokens": 10.0,
                                    "agent_seconds": 1.0,
                                    "judge_seconds": 0.0,
                                    "oracle_seconds": 0.5,
                                    "api_equivalent_usd_lower_bound": 0.008,
                                    "api_equivalent_usd": 0.01,
                                    "incremental_billed_usd": 0.0,
                                    "model_served": "model",
                                    "model_identity_matches": True,
                                    "oracle_valid": True,
                                    "manifest_digest": "manifest",
                                    "preregistration_commit": "freeze",
                                }
                            )
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "cycles.jsonl"
            log.write_text(
                "\n".join(json.dumps(record) for record in records) + "\n",
                encoding="utf-8",
            )
            with mock.patch.object(
                MODULE, "bootstrap_mean_interval", return_value=(0.0, 1.0)
            ):
                report = MODULE.analyze(log)

        self.assertEqual(report["trajectories"], 160)
        self.assertEqual(report["blocks"], 40)
        self.assertEqual(set(report["primary_tests"]), set(MODULE.PRIMARY_TESTS))
        self.assertIn("B-H2", report["secondary_tests"])
        self.assertIn("B-G1", report["secondary_tests"])
        self.assertGreater(
            report["secondary_tests"]["B-H2"]["estimate_minus_margin"], 0.0
        )
        self.assertIn("erosion_hob", report["tasks"]["s1_swebench"])
        self.assertIn(
            "gain_per_wall_clock_hour",
            report["tasks"]["s1_swebench"]["cost_and_efficiency"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
