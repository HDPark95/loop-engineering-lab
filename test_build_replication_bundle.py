#!/usr/bin/env python3
"""Tests for deterministic confirmatory replication packaging."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import build_replication_bundle as bundle
import replay
import run_measurement


class ReplicationBundleTest(unittest.TestCase):
    def test_candidate_tar_is_deterministic_and_digest_checked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "candidate"
            candidate.mkdir()
            (candidate / "source.py").write_text("value = 1\n", encoding="utf-8")
            archive = root / "archive"
            manifest_id = run_measurement.archive_candidate(candidate, archive)
            cycles = [
                {
                    "schema_version": 5,
                    "apparatus_test": False,
                    "candidate_archive_manifest_sha256": manifest_id,
                }
            ]
            files = bundle.archive_file_map(cycles, archive)
            first = root / "first.tar.gz"
            second = root / "second.tar.gz"
            bundle.deterministic_tar_gz(first, files)
            bundle.deterministic_tar_gz(second, files)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            bundle.verify_candidate_tar(first, set(files))
            first_zip = root / "first.zip"
            second_zip = root / "second.zip"
            bundle.deterministic_zip(first_zip, [first])
            bundle.deterministic_zip(second_zip, [first])
            self.assertEqual(first_zip.read_bytes(), second_zip.read_bytes())

    def test_report_digest_mismatch_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "report.json"
            path.write_text(json.dumps({"clean": True}), encoding="utf-8")
            value, payload = bundle.json_snapshot(path)
            self.assertEqual(value, {"clean": True})
            self.assertEqual(bundle.sha256_bytes(payload), replay.file_sha256(path))
            path.write_text("[]\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "root must be an object"):
                bundle.json_snapshot(path)

    def test_zenodo_metadata_has_two_orcids_and_the_preprint_relation(self) -> None:
        creators = bundle.ZENODO_METADATA["creators"]
        self.assertEqual(bundle.ZENODO_METADATA["license"], "mit")
        self.assertEqual(
            {creator["orcid"] for creator in creators},
            {"0009-0005-4870-8803", "0009-0004-4987-3897"},
        )
        self.assertIn(
            "10.5281/zenodo.21594735",
            {
                relation["identifier"]
                for relation in bundle.ZENODO_METADATA["related_identifiers"]
            },
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
