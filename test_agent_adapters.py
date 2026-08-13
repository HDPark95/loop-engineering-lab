#!/usr/bin/env python3
"""Unit tests for command construction and aggregate usage parsing."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
        self.assertIn("type=bind,src=/tmp/auth.json,dst=/tmp/.codex/auth.json,readonly", command)
        position = command.index("--user")
        self.assertEqual(command[position + 1], f"{os.getuid()}:{os.getgid()}")
        self.assertIn("HOME=/tmp", command)
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

    def test_subscription_cost_separates_cli_estimate_from_billing(self):
        cost = agent_adapters.cost_fields(
            agent_adapters.Usage(30, 8, 4, 0.012), "subscription"
        )
        self.assertEqual(cost["cli_reported_usd"], 0.012)
        self.assertEqual(cost["incremental_billed_usd"], 0.0)

    def test_api_cost_uses_cli_report_as_incremental_billing(self):
        cost = agent_adapters.cost_fields(agent_adapters.Usage(30, 8, 4, 0.012), "api")
        self.assertEqual(cost["incremental_billed_usd"], 0.012)

    def test_unknown_cost_does_not_infer_incremental_billing(self):
        cost = agent_adapters.cost_fields(agent_adapters.Usage(30, 8, 4, None), "unknown")
        self.assertIsNone(cost["incremental_billed_usd"])

    def test_invalid_billing_mode_fails_before_external_work(self):
        with mock.patch.object(
            agent_adapters.shutil, "which", side_effect=AssertionError("external work started")
        ):
            with self.assertRaisesRegex(ValueError, "unsupported billing mode"):
                agent_adapters.run_smoke(
                    "codex", "s1", None, 1, 0.2, billing_mode="unsupported"
                )

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
        self.assertIn("type=bind,src=/tmp/state.json,dst=/tmp/.claude.json,readonly", command)

    def test_subscription_measurement_command_has_schema_without_api_budget_flag(self):
        command = agent_adapters.container_command_for(
            "claude",
            Path("/tmp/work"),
            "claude-test-20260801",
            0.2,
            "agent-image",
            auth_file=Path("/tmp/auth.json"),
            prompt="measure",
            verdict_schema=agent_adapters.VERDICT_SCHEMA,
            container_name="measurement-test",
            billing_mode="subscription",
        )
        self.assertIn("--json-schema", command)
        self.assertIn("measurement-test", command)
        self.assertNotIn("--max-budget-usd", command)
        self.assertEqual(command[-1], "measure")

    def test_structured_reports_and_runtime_model_are_parsed(self):
        report = {"improved": True, "confidence": 0.8, "evidence": "tests pass"}
        codex_output = json.dumps({
            "type": "item.completed",
            "item": {"type": "agent_message", "text": json.dumps(report)},
        })
        self.assertEqual(agent_adapters.parse_codex_final_report(codex_output), report)

        claude_output = json.dumps({
            "structured_output": report,
            "modelUsage": {"claude-test-20260801": {"inputTokens": 2}},
        })
        self.assertEqual(agent_adapters.parse_claude_final_report(claude_output), report)
        self.assertEqual(
            agent_adapters.reported_model("claude", claude_output),
            "claude-test-20260801",
        )

    def test_measurement_cycle_retains_only_structured_output_and_usage(self):
        report = {"improved": False, "confidence": 0.7, "evidence": "tests still fail"}
        stdout = json.dumps({
            "structured_output": report,
            "modelUsage": {"claude-test-20260801": {}},
            "usage": {"input_tokens": 30, "output_tokens": 8},
        })
        completed = mock.Mock(returncode=0, stdout=stdout, stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            auth = Path(tmp) / "auth.json"
            auth.write_text("{}", encoding="utf-8")
            with (
                mock.patch.object(
                    agent_adapters.shutil, "which", return_value="/usr/bin/docker"
                ),
                mock.patch.object(agent_adapters.subprocess, "run", return_value=completed),
            ):
                result = agent_adapters.run_measurement_cycle(
                    agent="claude",
                    model="claude-test-20260801",
                    task="s1",
                    workspace=workspace,
                    cycle=2,
                    feedback="rejected",
                    container_image="image@sha256:" + "a" * 64,
                    auth_file=auth,
                    state_file=None,
                    timeout_seconds=10,
                    billing_mode="subscription",
                    max_budget_usd=1.0,
                )
            self.assertEqual(result["self_report"], report)
            self.assertEqual(result["model_served"], "claude-test-20260801")
            self.assertEqual((result["input_tokens"], result["output_tokens"]), (30, 8))
            self.assertFalse((workspace / ".loop-verdict-schema.json").exists())

    def test_measurement_cycle_removes_schema_when_auth_copy_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            auth = Path(tmp) / "auth.json"
            auth.write_text("{}", encoding="utf-8")
            with (
                mock.patch.object(
                    agent_adapters.shutil, "which", return_value="/usr/bin/docker"
                ),
                mock.patch.object(
                    agent_adapters.shutil, "copy2", side_effect=OSError("copy failed")
                ),
                self.assertRaisesRegex(OSError, "copy failed"),
            ):
                agent_adapters.run_measurement_cycle(
                    agent="codex",
                    model="gpt-test",
                    task="s1",
                    workspace=workspace,
                    cycle=1,
                    feedback="",
                    container_image="image@sha256:" + "a" * 64,
                    auth_file=auth,
                    state_file=None,
                    timeout_seconds=10,
                    billing_mode="subscription",
                    max_budget_usd=1.0,
                )
            self.assertFalse((workspace / ".loop-verdict-schema.json").exists())

    def test_container_can_forward_auth_environment_by_name(self):
        command = agent_adapters.container_command_for(
            "claude", Path("/tmp/work"), "sonnet", 0.2, "agent-image", None, "ANTHROPIC_API_KEY"
        )
        position = command.index("ANTHROPIC_API_KEY")
        self.assertEqual(command[position - 1], "--env")
        self.assertNotIn("=", command[position])

    def test_container_can_use_a_private_environment_file(self):
        command = agent_adapters.container_command_for(
            "claude",
            Path("/tmp/work"),
            "sonnet",
            0.2,
            "agent-image",
            auth_env_file=Path("/tmp/private.env"),
        )
        position = command.index("--env-file")
        self.assertEqual(command[position + 1], "/tmp/private.env")
        self.assertNotIn("ANTHROPIC_API_KEY=", " ".join(command))

    def test_container_can_mount_a_disposable_auth_directory(self):
        command = agent_adapters.container_command_for(
            "codex",
            Path("/tmp/work"),
            None,
            0.2,
            "agent-image",
            auth_dir=Path("/tmp/private-auth"),
        )
        self.assertIn("type=bind,src=/tmp/private-auth,dst=/tmp/.codex", command)
        self.assertNotIn("/home/", " ".join(command))

    def test_tree_digest_changes_with_contents(self):
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            (root / "file.txt").write_text("one", encoding="utf-8")
            before = agent_adapters.tree_digest(root)
            (root / "file.txt").write_text("two", encoding="utf-8")
            self.assertNotEqual(before, agent_adapters.tree_digest(root))

    def test_tree_digest_hashes_a_symlink_without_following_its_target(self):
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            (root / "external").symlink_to("/path/that/must/not/be-read")
            self.assertEqual(len(agent_adapters.tree_digest(root)), 64)


if __name__ == "__main__":
    unittest.main()
