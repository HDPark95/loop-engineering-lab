#!/usr/bin/env python3
"""Unit tests for command construction and aggregate usage parsing."""

import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import agent_adapters


def claude_credentials(
    access_token: str = "access-before-token-test",
    refresh_token: str = "refresh-before-token-test",
    expires_at: int = 1000,
) -> dict:
    return {
        "claudeAiOauth": {
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "expiresAt": expires_at,
            "subscriptionType": "max",
            "rateLimitTier": "default_claude_max_20x",
            "scopes": ["user:inference", "user:profile"],
        },
        "unrelated": {"preserve": True},
    }


def codex_credentials() -> dict:
    return {
        "auth_mode": "chatgpt",
        "tokens": {
            "access_token": "codex-access-token-for-tests",
            "refresh_token": "codex-refresh-token-for-tests",
            "id_token": "codex-id-token-for-tests",
            "account_id": "account-metadata",
        },
    }


class AdapterTest(unittest.TestCase):
    def test_exact_credentials_are_rejected_from_output_and_candidate(self):
        secret = "exact-refresh-token-for-leak-test"
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            with self.assertRaises(agent_adapters.AgentInvocationError) as output_error:
                agent_adapters.reject_credential_leak(
                    workspace, f"prefix {secret} suffix", "", (secret,)
                )
            self.assertNotIn(secret, str(output_error.exception))

            (workspace / "candidate.txt").write_text(secret, encoding="utf-8")
            with self.assertRaises(agent_adapters.AgentInvocationError) as file_error:
                agent_adapters.reject_credential_leak(workspace, "", "", (secret,))
            self.assertNotIn(secret, str(file_error.exception))

    def test_validated_claude_refresh_updates_only_oauth_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.json"
            disposable = Path(tmp) / "disposable.json"
            before = claude_credentials()
            source.write_text(json.dumps(before), encoding="utf-8")
            source.chmod(0o600)
            refreshed = claude_credentials(
                "access-after-token-test", "refresh-after-token-test", 2000
            )
            refreshed["unrelated"] = {"agent": "must not persist"}
            disposable.write_text(json.dumps(refreshed), encoding="utf-8")
            disposable.chmod(0o600)

            self.assertTrue(
                agent_adapters.persist_refreshed_claude_credentials(
                    source, disposable, before
                )
            )
            written = json.loads(source.read_text(encoding="utf-8"))
            self.assertEqual(written["claudeAiOauth"], refreshed["claudeAiOauth"])
            self.assertEqual(written["unrelated"], {"preserve": True})
            self.assertEqual(source.stat().st_mode & 0o777, 0o600)

    def test_invalid_claude_refresh_is_rejected_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.json"
            disposable = Path(tmp) / "disposable.json"
            before = claude_credentials()
            source.write_text(json.dumps(before), encoding="utf-8")
            source.chmod(0o600)
            invalid = claude_credentials(
                "access-after-token-test", "refresh-after-token-test", 2000
            )
            invalid["claudeAiOauth"]["subscriptionType"] = "different"
            disposable.write_text(json.dumps(invalid), encoding="utf-8")
            disposable.chmod(0o600)

            with self.assertRaisesRegex(
                agent_adapters.AgentInvocationError, "account metadata"
            ):
                agent_adapters.persist_refreshed_claude_credentials(
                    source, disposable, before
                )
            self.assertEqual(json.loads(source.read_text(encoding="utf-8")), before)

    def test_claude_refresh_source_requires_exact_private_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.json"
            source.write_text(json.dumps(claude_credentials()), encoding="utf-8")
            source.chmod(0o700)
            with self.assertRaisesRegex(
                agent_adapters.AgentInvocationError, "mode 0600"
            ):
                agent_adapters._read_private_json(source)

    def test_confirmatory_claude_invocations_are_serialized(self):
        active = 0
        maximum = 0
        state_lock = threading.Lock()

        def fake_cycle(**_kwargs):
            nonlocal active, maximum
            with state_lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.05)
            with state_lock:
                active -= 1
            return {}

        arguments = {
            "agent": "claude",
            "model": "claude-test",
            "task": "s1",
            "workspace": Path("/tmp/work"),
            "cycle": 1,
            "feedback": "",
            "container_image": "sha256:" + "a" * 64,
            "auth_file": Path("/tmp/auth"),
            "state_file": None,
            "timeout_seconds": 1,
            "billing_mode": "subscription",
            "max_budget_usd": 1.0,
            "persist_refreshed_credentials": True,
        }
        with mock.patch.object(
            agent_adapters, "_run_measurement_cycle_unlocked", side_effect=fake_cycle
        ):
            threads = [
                threading.Thread(
                    target=agent_adapters.run_measurement_cycle,
                    kwargs=arguments,
                )
                for _ in range(2)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(maximum, 1)

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
        self.assertIn("/opt/loop-codex-app-server-client.py", command)
        self.assertTrue(
            any(
                "dst=/opt/loop-codex-app-server-client.py,readonly" in part
                for part in command
            )
        )
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
                "usage": {
                    "input_tokens": 30,
                    "output_tokens": 8,
                    "cache_read_input_tokens": 4,
                    "cache_creation_input_tokens": 6,
                    "cache_creation": {
                        "ephemeral_5m_input_tokens": 4,
                        "ephemeral_1h_input_tokens": 2,
                    },
                },
                "total_cost_usd": 0.012,
            }
        )
        usage = agent_adapters.parse_claude_usage(output)
        self.assertEqual((usage.input_tokens, usage.output_tokens, usage.cache_tokens), (30, 8, 4))
        self.assertEqual(usage.cache_creation_tokens, 6)
        self.assertEqual(usage.cache_creation_5m_tokens, 4)
        self.assertEqual(usage.cache_creation_1h_tokens, 2)
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

        app_server_output = json.dumps({
            "protocol": "codex-app-server-v2",
            "self_report": report,
            "model_served": "gpt-5.6-terra",
            "usage": {
                "input_tokens": 30,
                "cached_input_tokens": 4,
                "output_tokens": 8,
            },
        })
        self.assertEqual(agent_adapters.parse_codex_final_report(app_server_output), report)
        self.assertEqual(
            agent_adapters.reported_model("codex", app_server_output),
            "gpt-5.6-terra",
        )
        self.assertEqual(
            agent_adapters.parse_codex_usage(app_server_output),
            agent_adapters.Usage(30, 8, 4, None),
        )

        claude_output = json.dumps({
            "structured_output": report,
            "modelUsage": {"claude-test-20260801": {"inputTokens": 2}},
        })
        self.assertEqual(agent_adapters.parse_claude_final_report(claude_output), report)
        self.assertEqual(
            agent_adapters.reported_model("claude", claude_output),
            "claude-test-20260801",
        )

        claude_alias_output = json.dumps({
            "model": "sonnet",
            "modelUsage": {"claude-sonnet-4-test-20260801": {}},
        })
        self.assertEqual(
            agent_adapters.reported_model("claude", claude_alias_output),
            "claude-sonnet-4-test-20260801",
        )
        self.assertIsNone(
            agent_adapters.reported_model(
                "claude", json.dumps({"model": "sonnet", "modelUsage": {}})
            )
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
            auth.write_text(json.dumps(claude_credentials()), encoding="utf-8")
            auth.chmod(0o600)
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

    def test_measurement_cycle_mounts_only_sanitized_claude_state(self):
        report = {"improved": False, "confidence": 0.7, "evidence": "no change"}
        completed = mock.Mock(
            returncode=0,
            stdout=json.dumps(
                {
                    "structured_output": report,
                    "modelUsage": {"claude-test-20260801": {}},
                    "usage": {"input_tokens": 3, "output_tokens": 2},
                }
            ),
            stderr="",
        )
        observed = {}

        def inspect_runtime(command, **_kwargs):
            mounts = [
                command[index + 1]
                for index, value in enumerate(command[:-1])
                if value == "--mount"
            ]
            auth_mount = next(item for item in mounts if "dst=/tmp/.claude" in item)
            state_mount = next(item for item in mounts if "dst=/tmp/.claude.json" in item)
            auth_dir = Path(auth_mount.split("src=", 1)[1].split(",dst=", 1)[0])
            state_path = Path(state_mount.split("src=", 1)[1].split(",dst=", 1)[0])
            observed["settings"] = json.loads(
                (auth_dir / "settings.json").read_text(encoding="utf-8")
            )
            observed["state"] = json.loads(state_path.read_text(encoding="utf-8"))
            observed["state_mode"] = state_path.stat().st_mode & 0o777
            return completed

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            auth = Path(tmp) / "auth.json"
            auth.write_text(json.dumps(claude_credentials()), encoding="utf-8")
            auth.chmod(0o600)
            with (
                mock.patch.object(
                    agent_adapters.shutil, "which", return_value="/usr/bin/docker"
                ),
                mock.patch.object(
                    agent_adapters.subprocess, "run", side_effect=inspect_runtime
                ),
            ):
                agent_adapters.run_measurement_cycle(
                    agent="claude",
                    model="claude-test-20260801",
                    task="s1",
                    workspace=workspace,
                    cycle=1,
                    feedback="",
                    container_image="sha256:" + "a" * 64,
                    auth_file=auth,
                    state_file=None,
                    timeout_seconds=10,
                    billing_mode="subscription",
                    max_budget_usd=1.0,
                )
        self.assertEqual(observed["settings"], agent_adapters.SANITIZED_CLAUDE_SETTINGS)
        self.assertEqual(observed["state"], agent_adapters.SANITIZED_CLAUDE_STATE)
        self.assertEqual(observed["state_mode"], 0o600)
        serialized = json.dumps(observed)
        self.assertNotIn("oauthAccount", serialized)
        self.assertNotIn("/home/", serialized)

    def test_external_claude_state_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            auth = root / "auth.json"
            auth.write_text(json.dumps(claude_credentials()), encoding="utf-8")
            auth.chmod(0o600)
            state = root / "state.json"
            state.write_text("{}", encoding="utf-8")
            with (
                mock.patch.object(
                    agent_adapters.shutil, "which", return_value="/usr/bin/docker"
                ),
                self.assertRaisesRegex(
                    agent_adapters.AgentInvocationError, "external Claude state"
                ),
            ):
                agent_adapters.run_measurement_cycle(
                    agent="claude",
                    model="claude-test-20260801",
                    task="s1",
                    workspace=workspace,
                    cycle=1,
                    feedback="",
                    container_image="sha256:" + "a" * 64,
                    auth_file=auth,
                    state_file=state,
                    timeout_seconds=10,
                    billing_mode="subscription",
                    max_budget_usd=1.0,
                )

    def test_measurement_cycle_persists_a_valid_claude_rotation(self):
        report = {"improved": False, "confidence": 0.7, "evidence": "tests still fail"}
        stdout = json.dumps({
            "structured_output": report,
            "modelUsage": {"claude-test-20260801": {}},
            "usage": {"input_tokens": 30, "output_tokens": 8},
        })
        completed = mock.Mock(returncode=0, stdout=stdout, stderr="")

        def rotate(command, **_kwargs):
            auth_mount = next(
                value
                for value in command
                if isinstance(value, str) and "dst=/tmp/.claude" in value
            )
            auth_dir = Path(auth_mount.split("src=", 1)[1].split(",dst=", 1)[0])
            copied = auth_dir / ".credentials.json"
            copied.write_text(
                json.dumps(
                    claude_credentials(
                        "access-after-token-test", "refresh-after-token-test", 2000
                    )
                ),
                encoding="utf-8",
            )
            copied.chmod(0o600)
            return completed

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            auth = Path(tmp) / "auth.json"
            auth.write_text(json.dumps(claude_credentials()), encoding="utf-8")
            auth.chmod(0o600)
            with (
                mock.patch.object(
                    agent_adapters.shutil, "which", return_value="/usr/bin/docker"
                ),
                mock.patch.object(agent_adapters.subprocess, "run", side_effect=rotate),
            ):
                result = agent_adapters.run_measurement_cycle(
                    agent="claude",
                    model="claude-test-20260801",
                    task="s1",
                    workspace=workspace,
                    cycle=2,
                    feedback="rejected",
                    container_image="sha256:" + "a" * 64,
                    auth_file=auth,
                    state_file=None,
                    timeout_seconds=10,
                    billing_mode="subscription",
                    max_budget_usd=1.0,
                    persist_refreshed_credentials=True,
                )
            self.assertTrue(result["credential_refresh_persisted"])
            self.assertEqual(
                json.loads(auth.read_text(encoding="utf-8"))["claudeAiOauth"]["accessToken"],
                "access-after-token-test",
            )

    def test_measurement_cycle_removes_schema_when_auth_copy_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            auth = Path(tmp) / "auth.json"
            auth.write_text(json.dumps(codex_credentials()), encoding="utf-8")
            auth.chmod(0o600)
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

    def test_measurement_timeout_cleanup_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            auth = Path(tmp) / "auth.json"
            auth.write_text(json.dumps(codex_credentials()), encoding="utf-8")
            auth.chmod(0o600)
            timeout = subprocess.TimeoutExpired("docker", 10)
            with (
                mock.patch.object(
                    agent_adapters.shutil, "which", return_value="/usr/bin/docker"
                ),
                mock.patch.object(
                    agent_adapters.subprocess,
                    "run",
                    side_effect=[timeout, subprocess.TimeoutExpired("docker rm", 5)],
                ) as run,
                self.assertRaisesRegex(
                    agent_adapters.AgentInvocationError, "agent invocation timed out"
                ),
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
            self.assertEqual(
                run.call_args_list[-1].kwargs["timeout"],
                agent_adapters.DOCKER_CLEANUP_TIMEOUT_SECONDS,
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
        # The frozen App Server helper itself is mounted read-only. Apart from
        # that exact file, no source directory or user home may be exposed.
        mounts = [
            command[index + 1]
            for index, value in enumerate(command[:-1])
            if value == "--mount"
        ]
        expected_helper = (
            f"type=bind,src={agent_adapters.APP_SERVER_CLIENT_SOURCE},"
            "dst=/opt/loop-codex-app-server-client.py,readonly"
        )
        self.assertIn(expected_helper, mounts)
        self.assertFalse(
            any("src=/home/" in mount for mount in mounts if mount != expected_helper)
        )

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
