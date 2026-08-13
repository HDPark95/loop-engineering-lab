#!/usr/bin/env python3
"""Tests for the non-self-referential manifest binding step."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import finalize_measurement_manifest as finalizer


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
