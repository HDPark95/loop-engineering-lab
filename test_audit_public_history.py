#!/usr/bin/env python3
"""Tests for the fail-closed public Git history audit."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import audit_public_history as audit


class PublicHistoryAuditTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        subprocess.run(["git", "init", "-q", self.root], check=True)
        self.environment = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Test Author",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test Author",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def commit(self, message: str = "Safe commit") -> None:
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", message],
            cwd=self.root,
            env=self.environment,
            check=True,
        )

    def test_safe_history_is_clean(self) -> None:
        (self.root / "README.md").write_text("Public study\n", encoding="utf-8")
        self.commit()
        findings, counts = audit.scan(self.root)
        self.assertEqual([], findings)
        self.assertEqual(1, counts["reachable_commits"])

    def test_credentials_and_forbidden_commit_metadata_are_rejected(self) -> None:
        (self.root / "secret.txt").write_text(
            "sk-ant-" + "a" * 40 + "\n", encoding="utf-8"
        )
        self.commit("Mention " + "async" + "site")
        findings, _counts = audit.scan(self.root)
        self.assertEqual(
            {"anthropic-key", "organization-1"},
            {finding.pattern for finding in audit.unexpected_findings(findings)},
        )

    def test_exact_historical_exception_does_not_allow_reintroduction(self) -> None:
        path = self.root / "instance.json"
        path.write_text("private/" + "async" + "site/frame\n", encoding="utf-8")
        self.commit()
        object_id = subprocess.check_output(
            ["git", "rev-parse", "HEAD:instance.json"], cwd=self.root, text=True
        ).strip()
        path.write_text("public/frame\n", encoding="utf-8")
        self.commit()
        allowed = {(object_id, "instance.json", "organization-1", 1)}
        findings, _counts = audit.scan(self.root)
        with mock.patch.object(audit, "ALLOWED_HISTORICAL_FINDINGS", allowed):
            self.assertEqual([], audit.unexpected_findings(findings))
            path.write_text(
                "private/" + "async" + "site/frame\n", encoding="utf-8"
            )
            self.commit()
            findings, _counts = audit.scan(self.root)
            self.assertTrue(
                any(
                    item.scope == "current-tree"
                    for item in audit.unexpected_findings(findings)
                )
            )

    def test_changed_historical_exception_count_is_rejected(self) -> None:
        (self.root / "instance.json").write_text(
            ("async" + "site ") * 2 + "\n", encoding="utf-8"
        )
        self.commit()
        object_id = subprocess.check_output(
            ["git", "rev-parse", "HEAD:instance.json"], cwd=self.root, text=True
        ).strip()
        findings, _counts = audit.scan(self.root)
        allowed = {(object_id, "instance.json", "organization-1", 1)}
        with mock.patch.object(audit, "ALLOWED_HISTORICAL_FINDINGS", allowed):
            self.assertTrue(audit.unexpected_findings(findings))


if __name__ == "__main__":
    unittest.main(verbosity=2)
