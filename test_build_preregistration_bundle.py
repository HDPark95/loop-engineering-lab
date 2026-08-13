#!/usr/bin/env python3
"""Tests for deterministic pre-outcome preregistration packaging."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import build_preregistration_bundle as bundle
import finalize_measurement_manifest


class PreregistrationBundleTest(unittest.TestCase):
    def test_metadata_is_distinct_from_the_confirmatory_replication_record(self):
        self.assertEqual(bundle.ZENODO_METADATA["version"], "prereg-v1")
        self.assertEqual(bundle.ZENODO_METADATA["license"], "mit")
        self.assertIn("Frozen Preregistration", bundle.ZENODO_METADATA["title"])
        self.assertEqual(
            {creator["orcid"] for creator in bundle.ZENODO_METADATA["creators"]},
            {"0009-0005-4870-8803", "0009-0004-4987-3897"},
        )

    def test_runtime_digest_binding_detects_any_changed_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "template.json"
            template.write_text("{}\n", encoding="utf-8")
            inputs = {}
            evidence = {"schema_version": 1}
            fields = {
                "alias_smoke_sha256": "alias-smoke",
                "exact_smoke_sha256": "exact-smoke",
                "pricing_record_sha256": "pricing",
                "claude_apparatus_manifest_sha256": "claude-manifest",
                "claude_apparatus_log_sha256": "claude-log",
                "claude_apparatus_resources_sha256": "claude-resources",
            }
            for field, name in fields.items():
                path = root / name
                path.write_text(f"{name}\n", encoding="utf-8")
                inputs[name] = path
                evidence[field] = bundle.file_sha256(path)
            evidence["filled_template_sha256"] = bundle.file_sha256(template)
            bundle.validate_runtime_hashes(evidence, template, inputs)
            inputs["pricing"].write_text("changed\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "pricing_record_sha256"):
                bundle.validate_runtime_hashes(evidence, template, inputs)

    def test_freeze_binding_changes_only_the_commit_sentinel(self):
        template = {
            "preregistration_commit": finalize_measurement_manifest.FREEZE_SENTINEL,
            "other": "fixed",
        }
        bound = finalize_measurement_manifest.bind_freeze_commit(template, "a" * 40)
        self.assertEqual(bound["preregistration_commit"], "a" * 40)
        self.assertEqual(bound["other"], "fixed")

    def test_json_object_rejects_non_object_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "value.json"
            path.write_text(json.dumps([]), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "root must be an object"):
                bundle.json_object(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
