#!/usr/bin/env python3
"""Tests for pre-freeze runtime evidence binding and shadow-budget sizing."""

from __future__ import annotations

import argparse
import json
import shutil
import stat
import tempfile
import unittest
from pathlib import Path

import finalize_measurement_manifest
import prepare_runtime_manifests as prepare
import run_measurement


ROOT = Path(__file__).resolve().parent


class PrepareRuntimeManifestsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.model = "claude-sonnet-test-20260815"
        self.alias_smoke = self.root / "alias.json"
        self.exact_smoke = self.root / "exact.json"
        self.pricing = self.root / "pricing.json"
        self.apparatus_manifest = self.root / "apparatus.manifest.json"
        self.claude_log = self.root / "claude.cycles.jsonl"
        self.write_json(self.alias_smoke, self.smoke("sonnet", self.model))
        self.write_json(self.exact_smoke, self.smoke(self.model, self.model))
        self.write_json(self.pricing, self.pricing_record())
        manifest = prepare.build_apparatus_manifest(
            ROOT / "logs/apparatus/claude-resource-20260815.manifest.template.json",
            self.apparatus_manifest,
            self.alias_smoke,
            self.exact_smoke,
            self.pricing,
        )
        self.apparatus_digest = run_measurement.manifest_digest(manifest)
        self.write_cycles(
            self.claude_log,
            [self.claude_row(cycle) for cycle in range(1, 7)],
        )

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def write_json(path: Path, value: dict) -> None:
        path.write_text(json.dumps(value), encoding="utf-8")

    @staticmethod
    def write_cycles(path: Path, rows: list[dict]) -> None:
        path.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
            encoding="utf-8",
        )

    @staticmethod
    def smoke(requested: str, served: str) -> dict:
        return {
            "schema_version": 2,
            "agent": "claude",
            "model_requested": requested,
            "model_served": served,
            "process_returncode": 0,
            "credential_leak_scan_passed": True,
            "execution": {"model_completed": True},
            "public_tests": {"passed": True},
        }

    def pricing_record(self) -> dict:
        return {
            "schema_version": 1,
            "model": self.model,
            "pricing_schedule_id": "anthropic-claude-test-standard-20260815",
            "pricing_source_url": prepare.OFFICIAL_CLAUDE_PRICING_URL,
            "pricing_retrieved_utc": "2026-08-15T09:00:00Z",
            "usd_per_1k_input": 0.003,
            "usd_per_1k_cached_input": 0.0003,
            "usd_per_1k_output": 0.015,
            "cache_write_input_multiplier": 1.25,
            "cache_write_1h_input_multiplier": 2.0,
            "long_context_threshold_input_tokens": 200000,
            "long_context_input_multiplier": 2.0,
            "long_context_output_multiplier": 1.5,
        }

    def claude_row(self, cycle: int) -> dict:
        return {
            "schema_version": run_measurement.SCHEMA_VERSION,
            "attempt_id": "claude-resource-test:attempt-1",
            "manifest_digest": self.apparatus_digest,
            "trajectory": (
                f"s1_swebench|claude|{self.model}|grounded-numeric|11"
            ),
            "cycle": cycle,
            "cycles_planned": 6,
            "apparatus_test": True,
            "task": "s1_swebench",
            "agent": "claude",
            "seed": 11,
            "cell": "grounded-numeric",
            "model_served": self.model,
            "model_identity_matches": True,
            "credential_leak_scan_passed": True,
            "cost_allocation_fraction": 1.0,
            "input_tokens": 1100,
            "uncached_input_tokens": 1000,
            "cached_input_tokens": 100,
            "cache_write_input_tokens": 300,
            "cache_write_5m_input_tokens": 200,
            "cache_write_1h_input_tokens": 100,
            "cache_write_input_tokens_exact": True,
            "output_tokens": 100,
        }

    def test_alias_and_exact_model_evidence_must_agree(self):
        self.write_json(
            self.exact_smoke,
            self.smoke(self.model, "claude-different-20260815"),
        )
        with self.assertRaisesRegex(RuntimeError, "served different models"):
            prepare.validate_runtime_evidence(
                self.alias_smoke, self.exact_smoke, self.pricing
            )

    def test_pricing_schema_is_exact_and_official(self):
        malformed = self.pricing_record()
        malformed["unexpected"] = True
        self.write_json(self.pricing, malformed)
        with self.assertRaisesRegex(RuntimeError, "wrong schema or field set"):
            prepare.validate_runtime_evidence(
                self.alias_smoke, self.exact_smoke, self.pricing
            )

    def test_confirmatory_template_must_match_the_full_pricing_record(self):
        manifest = json.loads(
            (ROOT / "measurement-manifest.template.json").read_text(encoding="utf-8")
        )
        manifest = prepare.replace_values(
            manifest,
            prepare.confirmatory_replacements(
                self.model, self.pricing_record(), 20.0
            ),
        )
        pricing = self.pricing_record()
        pricing["cache_write_input_multiplier"] = 1.5
        with self.assertRaisesRegex(RuntimeError, "does not reproduce"):
            prepare.validate_confirmatory_pricing_binding(
                manifest, self.model, pricing
            )

    def test_all_long_context_upper_uses_exact_cache_write_tiers(self):
        upper = prepare.all_long_context_upper(
            self.claude_row(1), self.pricing_record()
        )
        self.assertAlmostEqual(upper, 0.00921)
        row = self.claude_row(1)
        row["cache_write_input_tokens_exact"] = False
        with self.assertRaisesRegex(RuntimeError, "exact cache-write"):
            prepare.all_long_context_upper(row, self.pricing_record())

    def test_apparatus_log_must_bind_the_generated_manifest(self):
        rows = [self.claude_row(cycle) for cycle in range(1, 7)]
        rows[0]["manifest_digest"] = "0" * 64
        self.write_cycles(self.claude_log, rows)
        with self.assertRaisesRegex(RuntimeError, "frozen apparatus contract"):
            prepare.completed_apparatus_rows(
                self.claude_log, self.model, self.apparatus_digest
            )

    def test_conservative_estimate_is_deterministic_and_has_a_floor(self):
        self.assertEqual(prepare.conservative_estimate(2.0, 3.0), 20.0)
        self.assertEqual(prepare.conservative_estimate(5.01, 3.0), 21.0)

    def test_apparatus_manifest_is_valid_and_exclusive(self):
        output = self.root / "apparatus.json"
        filled = prepare.build_apparatus_manifest(
            ROOT / "logs/apparatus/claude-resource-20260815.manifest.template.json",
            output,
            self.alias_smoke,
            self.exact_smoke,
            self.pricing,
        )
        self.assertEqual(filled["agents"][0]["model"], self.model)
        self.assertEqual(run_measurement.load_manifest(output), filled)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o444)
        with self.assertRaisesRegex(RuntimeError, "refusing to overwrite"):
            prepare.build_apparatus_manifest(
                ROOT / "logs/apparatus/claude-resource-20260815.manifest.template.json",
                output,
                self.alias_smoke,
                self.exact_smoke,
                self.pricing,
            )

    def test_confirmatory_template_and_formula_evidence_are_valid(self):
        template = self.root / "measurement-manifest.template.json"
        shutil.copy2(ROOT / "measurement-manifest.template.json", template)
        (self.root / "preflight").mkdir()
        shutil.copy2(
            ROOT / "preflight/prereg-v1-candidate-sandbox.json",
            self.root / "preflight/prereg-v1-candidate-sandbox.json",
        )
        output = self.root / "measurement-manifest.runtime.template.json"
        evidence_output = self.root / "runtime-budget-evidence.json"
        args = argparse.Namespace(
            alias_smoke=self.alias_smoke,
            exact_smoke=self.exact_smoke,
            pricing=self.pricing,
            claude_manifest=self.apparatus_manifest,
            claude_log=self.claude_log,
            template=template,
            output=output,
            evidence_output=evidence_output,
        )
        filled = prepare.build_confirmatory_template(args)
        self.assertEqual(
            finalize_measurement_manifest.unresolved_value_sentinels(filled), []
        )
        self.assertEqual(
            filled["preregistration_commit"],
            finalize_measurement_manifest.FREEZE_SENTINEL,
        )
        self.assertEqual(
            filled["estimated_api_equivalent_usd_per_trajectory"], 57.0
        )
        validation = dict(filled)
        validation["preregistration_commit"] = "0" * 40
        prepare.validate_manifest_data(output, validation)
        evidence = json.loads(evidence_output.read_text(encoding="utf-8"))
        self.assertEqual(evidence["claude_all_long_context_upper_usd"], 0.05526)
        self.assertEqual(
            evidence["estimated_api_equivalent_usd_per_trajectory"], 57.0
        )
        self.assertEqual(evidence["filled_template_sha256"], prepare.file_sha256(output))
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o444)
        self.assertEqual(stat.S_IMODE(evidence_output.stat().st_mode), 0o444)


if __name__ == "__main__":
    unittest.main(verbosity=2)
