#!/usr/bin/env python3
"""Static guards for container inputs that must not float after freeze."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FROM = re.compile(r"^FROM\s+\S+@sha256:([0-9a-f]{64})(?:\s|$)", re.MULTILINE)


class ContainerReproducibilityTest(unittest.TestCase):
    def test_every_measurement_base_image_is_digest_pinned(self):
        dockerfiles = [
            ROOT / "docker" / "se-agent.Dockerfile",
            ROOT / "docker" / "se-sandbox.Dockerfile",
        ]
        for path in dockerfiles:
            text = path.read_text(encoding="utf-8")
            from_lines = [line for line in text.splitlines() if line.startswith("FROM ")]
            self.assertTrue(from_lines, path)
            self.assertEqual(len(FROM.findall(text)), len(from_lines), path)

    def test_agent_cli_versions_are_exact(self):
        text = (ROOT / "docker" / "se-agent.Dockerfile").read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^ARG CLAUDE_CODE_VERSION=\d+\.\d+\.\d+$")
        self.assertRegex(text, r"(?m)^ARG CODEX_VERSION=\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
