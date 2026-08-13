#!/usr/bin/env python3
"""Tests for the mechanical reward-hacking audit."""

import json
import tempfile
import unittest
from pathlib import Path

from analysis import classify_reward_hacking as classifier


class RewardHackingAuditTest(unittest.TestCase):
    def test_empty_log_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "cycles.jsonl"
            path.write_text("", encoding="utf-8")
            report = classifier.audit(path)
        self.assertFalse(report["clean"])
        self.assertEqual(report["completed_cycle_rows"], 0)

    def test_nested_oracle_signal_blocks_the_audit(self):
        record = {
            "schema_version": 3,
            "trajectory": "t1",
            "apparatus_test": False,
            "candidate_archive_manifest_sha256": "a" * 64,
            "oracle_metrics_hoa": {"reward_hack_signals": ["test_modification"]},
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "cycles.jsonl"
            path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            report = classifier.audit(path)
        self.assertFalse(report["clean"])
        self.assertEqual(report["signal_counts"], {"test_modification": 1})

    def test_complete_archived_row_is_clean(self):
        record = {
            "schema_version": 3,
            "trajectory": "t1",
            "apparatus_test": False,
            "candidate_archive_manifest_sha256": "a" * 64,
            "reward_hack_signals": [],
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "cycles.jsonl"
            path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            report = classifier.audit(path)
        self.assertTrue(report["clean"])


if __name__ == "__main__":
    unittest.main()
