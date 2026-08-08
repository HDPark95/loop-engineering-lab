#!/usr/bin/env python3
"""Unit tests for command construction and aggregate usage parsing."""

import json
import tempfile
import unittest
from pathlib import Path

import agent_adapters


class AdapterTest(unittest.TestCase):
    def test_codex_command_is_ephemeral_and_workspace_scoped(self):
        command = agent_adapters.command_for("codex", Path("/tmp/work"), "gpt-test", 0.25)
        self.assertIn("--ephemeral", command)
        self.assertIn("workspace-write", command)
        self.assertIn("/tmp/work", command)
        self.assertIn("gpt-test", command)

    def test_claude_command_has_budget_and_no_persistence(self):
        command = agent_adapters.command_for("claude", Path("/tmp/work"), "sonnet", 0.2)
        self.assertIn("--max-budget-usd", command)
        self.assertIn("0.2", command)
        self.assertIn("--no-session-persistence", command)

    def test_container_command_exposes_only_workspace_and_auth(self):
        command = agent_adapters.container_command_for(
            "codex", Path("/tmp/work"), None, 0.2, "agent-image", Path("/tmp/auth.json")
        )
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", command)
        self.assertIn("type=bind,src=/tmp/work,dst=/workspace", command)
        self.assertIn("type=bind,src=/tmp/auth.json,dst=/home/node/.codex/auth.json,readonly", command)
        self.assertIn("node", command)
        self.assertNotIn("/oracle", " ".join(command))

    def test_codex_usage_parser(self):
        output = "\n".join(
            [
                json.dumps({"type": "turn.started"}),
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {"input_tokens": 20, "cached_input_tokens": 5, "output_tokens": 7},
                    }
                ),
            ]
        )
        usage = agent_adapters.parse_codex_usage(output)
        self.assertEqual((usage.input_tokens, usage.output_tokens, usage.cache_tokens), (20, 7, 5))

    def test_claude_usage_parser(self):
        output = json.dumps(
            {
                "usage": {"input_tokens": 30, "output_tokens": 8, "cache_read_input_tokens": 4},
                "total_cost_usd": 0.012,
            }
        )
        usage = agent_adapters.parse_claude_usage(output)
        self.assertEqual((usage.input_tokens, usage.output_tokens, usage.cache_tokens), (30, 8, 4))
        self.assertEqual(usage.usd, 0.012)

    def test_claude_api_error_is_not_completion(self):
        output = json.dumps({"is_error": True, "api_error_status": 429, "result": "rate limited"})
        self.assertEqual(
            agent_adapters.execution_status("claude", output, 1),
            {"model_completed": False, "api_error_status": 429, "error_kind": "quota"},
        )

    def test_claude_container_accepts_separate_state_file(self):
        command = agent_adapters.container_command_for(
            "claude",
            Path("/tmp/work"),
            "sonnet",
            0.2,
            "agent-image",
            Path("/tmp/auth.json"),
            None,
            Path("/tmp/state.json"),
        )
        self.assertIn("type=bind,src=/tmp/state.json,dst=/home/node/.claude.json,readonly", command)

    def test_container_can_forward_auth_environment_by_name(self):
        command = agent_adapters.container_command_for(
            "claude", Path("/tmp/work"), "sonnet", 0.2, "agent-image", None, "ANTHROPIC_API_KEY"
        )
        position = command.index("--env")
        self.assertEqual(command[position + 1], "ANTHROPIC_API_KEY")
        self.assertNotIn("=", command[position + 1])

    def test_tree_digest_changes_with_contents(self):
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            (root / "file.txt").write_text("one", encoding="utf-8")
            before = agent_adapters.tree_digest(root)
            (root / "file.txt").write_text("two", encoding="utf-8")
            self.assertNotEqual(before, agent_adapters.tree_digest(root))


if __name__ == "__main__":
    unittest.main()
