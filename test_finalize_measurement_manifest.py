#!/usr/bin/env python3
"""Tests for the non-self-referential manifest binding step."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import finalize_measurement_manifest as finalizer
import run_measurement


ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / "measurement-manifest.template.json"
CLAUDE_RESOURCE_TEMPLATE = (
    ROOT / "logs" / "apparatus" / "claude-resource-20260815.manifest.template.json"
)


class FinalizeManifestTest(unittest.TestCase):
    def test_bind_replaces_only_the_explicit_sentinel(self):
        template = {
            "preregistration_commit": finalizer.FREEZE_SENTINEL,
            "nested": {"value": finalizer.FREEZE_SENTINEL},
        }
        commit = "a" * 40
        bound = finalizer.bind_freeze_commit(template, commit)
        self.assertEqual(commit, bound["preregistration_commit"])
        self.assertEqual(finalizer.FREEZE_SENTINEL, bound["nested"]["value"])
        self.assertEqual(finalizer.FREEZE_SENTINEL, template["preregistration_commit"])

    def test_prefilled_commit_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "must be the freeze sentinel"):
            finalizer.bind_freeze_commit({"preregistration_commit": "a" * 40}, "b" * 40)

    def test_unresolved_runtime_values_are_reported_by_json_path(self):
        template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
        unresolved = finalizer.unresolved_value_sentinels(template)
        self.assertEqual(
            unresolved,
            [
                "$.agents[1].long_context_input_multiplier",
                "$.agents[1].long_context_output_multiplier",
                "$.agents[1].long_context_threshold_input_tokens",
                "$.agents[1].model",
                "$.agents[1].pricing_retrieved_utc",
                "$.agents[1].pricing_schedule_id",
                "$.agents[1].usd_per_1k_cached_input",
                "$.agents[1].usd_per_1k_input",
                "$.agents[1].usd_per_1k_output",
                "$.estimated_api_equivalent_usd_per_trajectory",
            ],
        )

    def test_claude_resource_template_has_only_three_runtime_values(self):
        template = json.loads(CLAUDE_RESOURCE_TEMPLATE.read_text(encoding="utf-8"))
        self.assertEqual(
            finalizer.unresolved_value_sentinels(template),
            [
                "$.agents[0].model",
                "$.agents[0].usd_per_1k_input",
                "$.agents[0].usd_per_1k_output",
            ],
        )
        template["agents"][0].update(
            {
                "model": "claude-test-2026-08-15",
                "usd_per_1k_input": 0.003,
                "usd_per_1k_output": 0.015,
            }
        )
        self.assertEqual(finalizer.unresolved_value_sentinels(template), [])
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "apparatus.json"
            manifest_path.write_text(json.dumps(template), encoding="utf-8")
            self.assertEqual(run_measurement.load_manifest(manifest_path), template)

    def test_filled_template_is_a_complete_confirmatory_manifest(self):
        template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
        claude = template["agents"][1]
        claude.update(
            {
                "long_context_input_multiplier": 1.0,
                "long_context_output_multiplier": 1.0,
                "long_context_threshold_input_tokens": 1000000,
                "model": "claude-test-2026-08-15",
                "pricing_retrieved_utc": "2026-08-15T09:00:00Z",
                "pricing_schedule_id": "anthropic-claude-test-standard-20260815",
                "usd_per_1k_cached_input": 0.0003,
                "usd_per_1k_input": 0.003,
                "usd_per_1k_output": 0.015,
            }
        )
        template["estimated_api_equivalent_usd_per_trajectory"] = 3.0
        bound = finalizer.bind_freeze_commit(template, "a" * 40)
        self.assertEqual(finalizer.unresolved_value_sentinels(bound), [])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "preflight").mkdir()
            shutil.copy2(
                ROOT / template["isolation_preflight_record"],
                root / template["isolation_preflight_record"],
            )
            manifest_path = root / "measurement-manifest.json"
            manifest_path.write_text(json.dumps(bound), encoding="utf-8")
            loaded = run_measurement.load_manifest(manifest_path)
            self.assertEqual(loaded, bound)
            trajectories = run_measurement.trajectories(loaded)
            groups = {
                run_measurement.common_group(trajectory)
                for trajectory in trajectories
            }
            self.assertEqual(len(trajectories), 160)
            self.assertEqual(len(trajectories) * loaded["cycles"], 960)
            self.assertEqual(
                len(groups) + len(trajectories) * (loaded["cycles"] - 1),
                840,
            )
            self.assertEqual(
                run_measurement.worker_lane_limits(loaded),
                {"claude": 1, "codex": 1},
            )
            self.assertGreaterEqual(
                loaded["quota_wait_seconds"] * loaded["quota_max_retries"],
                7 * 24 * 60 * 60,
            )

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "manifest.json"
            output.write_text("preserve me\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "refusing to overwrite"):
                finalizer.atomic_validated_write(output, {})
            self.assertEqual("preserve me\n", output.read_text(encoding="utf-8"))

    def test_invalid_manifest_leaves_no_partial_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "manifest.json"
            with self.assertRaises(SystemExit):
                finalizer.atomic_validated_write(output, {})
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
