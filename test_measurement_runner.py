"""Tests for the confirmatory runner and the replay tool.

The properties under test are the ones that decide whether a long run survives
contact with reality: it refuses to start on an unfrozen or aliased manifest, it
does not redo completed work, it stops on the budget boundary rather than past
it, and every reported number comes back out of the log without touching an
agent.

A scripted driver stands in for the coding agent so all of this is exercised
without credentials or spend. The manifest that reaches the scripted driver
cannot pass the confirmatory checks, which is deliberate.
"""

from __future__ import annotations

import json
import io
import hashlib
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import replay
import run_measurement

ROOT = Path(__file__).resolve().parent


def frozen_price() -> dict:
    return {
        "usd_per_1k_input": 0.1,
        "usd_per_1k_cached_input": 0.01,
        "usd_per_1k_output": 0.2,
        "cache_write_input_multiplier": 1.25,
        "cache_write_1h_input_multiplier": 2.0,
        "long_context_threshold_input_tokens": 272000,
        "long_context_input_multiplier": 2.0,
        "long_context_output_multiplier": 1.5,
        "pricing_schedule_id": "test-price-20260813",
        "pricing_source_url": "https://example.test/pricing",
        "pricing_retrieved_utc": "2026-08-13T00:00:00Z",
    }


def manifest(**overrides) -> dict:
    base = {
        "tasks": ["s1"],
        "agents": [
            {
                "name": "scripted",
                "model": "scripted-v1",
                "usd_per_1k_input": 0.003,
                "usd_per_1k_output": 0.015,
            }
        ],
        "cells": ["grounded-numeric", "ungrounded-numeric"],
        "seeds": [1, 2],
        "cycles": 2,
        "billing_mode": "subscription",
        "execution_mode": "prompt",
        "incremental_billed_usd": 0.0,
        "apparatus_test": True,
        "max_concurrent_agents": 3,
        "quota_wait_seconds": 3600,
        "quota_max_retries": 168,
        "cell_schedule_seed": "test-cell-order-v1",
        "estimated_api_equivalent_usd_per_trajectory": 0.01,
        "preregistration_commit": "0" * 40,
    }
    base.update(overrides)
    return base


def write_manifest(directory: Path, data: dict) -> Path:
    path = directory / "manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def add_isolation_preflight(directory: Path, data: dict) -> dict:
    image = data["candidate_sandbox_image"]
    record = directory / "isolation-preflight.json"
    payload = json.dumps(
        {
            "passed": True,
            "sandbox_image_requested": image,
            "sandbox_image_resolved": image,
        },
        sort_keys=True,
    ).encode()
    record.write_bytes(payload)
    data["isolation_preflight_record"] = record.name
    data["isolation_preflight_sha256"] = hashlib.sha256(payload).hexdigest()
    return data


def run_cli(manifest_path: Path, log_path: Path, run_id: str, *extra) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "run_measurement.py"),
            "--manifest", str(manifest_path),
            "--log", str(log_path),
            "--run-id", run_id,
            *extra,
        ],
        capture_output=True,
        text=True,
    )


class ManifestGateTest(unittest.TestCase):
    def test_nonplan_confirmatory_run_requires_public_preregistration(self):
        with self.assertRaisesRegex(
            run_measurement.zenodo_preregistration.ZenodoError,
            "public preregistration",
        ):
            run_measurement.enforce_external_preregistration_gate(
                {"apparatus_test": False}, False, None
            )
        run_measurement.enforce_external_preregistration_gate(
            {"apparatus_test": False}, True, None
        )
        run_measurement.enforce_external_preregistration_gate(
            {"apparatus_test": True}, False, None
        )
        run_measurement.enforce_external_preregistration_gate(
            {"apparatus_test": False}, False, {"verified": True}
        )

    def test_resume_refuses_different_external_preregistration(self):
        publication = {
            "external_preregistration_doi": "10.5281/zenodo.12345678",
            "external_preregistration_evidence_sha256": "a" * 64,
        }
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "cycles.jsonl"
            log.write_text(
                json.dumps(
                    {
                        "schema_version": run_measurement.SCHEMA_VERSION,
                        "manifest_digest": "manifest",
                        **publication,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            run_measurement.validate_existing_external_preregistration(
                log, "manifest", publication
            )
            changed = publication | {
                "external_preregistration_doi": "10.5281/zenodo.87654321"
            }
            with self.assertRaisesRegex(
                run_measurement.zenodo_preregistration.ZenodoError,
                "different external",
            ):
                run_measurement.validate_existing_external_preregistration(
                    log, "manifest", changed
                )

    def test_confirmatory_run_requires_candidate_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_manifest(
                Path(tmp),
                manifest(
                    apparatus_test=False,
                    candidate_sandbox_image="sandbox@sha256:" + "b" * 64,
                ),
            )
            with self.assertRaises(SystemExit) as caught:
                run_measurement.load_manifest(path)
            self.assertIn("artifact_archive_dir", str(caught.exception))

    def test_an_alias_model_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_manifest(
                Path(tmp),
                manifest(agents=[{"name": "claude", "model": "sonnet",
                                  "usd_per_1k_input": 0.003, "usd_per_1k_output": 0.015}]),
            )
            with self.assertRaises(SystemExit) as caught:
                run_measurement.load_manifest(path)
            self.assertIn("alias", str(caught.exception))

    def test_an_unfrozen_preregistration_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_manifest(Path(tmp), manifest(preregistration_commit=""))
            with self.assertRaises(SystemExit) as caught:
                run_measurement.load_manifest(path)
            self.assertIn("frozen", str(caught.exception))

    def test_a_missing_price_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_manifest(
                Path(tmp),
                manifest(agents=[{"name": "scripted", "model": "scripted-v1"}]),
            )
            with self.assertRaises(SystemExit):
                run_measurement.load_manifest(path)

    def test_subscription_mode_rejects_nonzero_incremental_billing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_manifest(Path(tmp), manifest(incremental_billed_usd=1.0))
            with self.assertRaises(SystemExit) as caught:
                run_measurement.load_manifest(path)
            self.assertIn("zero incremental", str(caught.exception))

    def test_prompt_mode_and_concurrency_cap_are_frozen(self):
        with tempfile.TemporaryDirectory() as tmp:
            for override in ({"execution_mode": "api"}, {"max_concurrent_agents": 4}):
                path = write_manifest(Path(tmp), manifest(**override))
                with self.assertRaises(SystemExit):
                    run_measurement.load_manifest(path)

    def test_negative_and_nonfinite_money_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            invalid_manifests = [
                manifest(
                    agents=[{
                        "name": "scripted",
                        "model": "scripted-v1",
                        "usd_per_1k_input": -0.1,
                        "usd_per_1k_output": 0.1,
                    }]
                ),
                manifest(
                    agents=[{
                        "name": "scripted",
                        "model": "scripted-v1",
                        "usd_per_1k_input": float("nan"),
                        "usd_per_1k_output": 0.1,
                    }]
                ),
                manifest(billing_mode="api", cost_ceiling_usd=float("inf")),
            ]
            for index, data in enumerate(invalid_manifests):
                path = directory / f"manifest-{index}.json"
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(SystemExit):
                    run_measurement.load_manifest(path)

    def test_real_agent_requires_frozen_container_and_runtime_auth_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            entry = {
                "name": "codex",
                "model": "gpt-test-2026-08-01",
                **frozen_price(),
                "container_image": "agent-image:latest",
                "timeout_seconds": 900,
                "auth_file_env": "LOOP_CODEX_AUTH_FILE",
                "persist_refreshed_credentials": True,
            }
            path = write_manifest(directory, manifest(agents=[entry]))
            with self.assertRaisesRegex(SystemExit, "sha256"):
                run_measurement.load_manifest(path)

            entry["container_image"] = "agent-image@sha256:" + "a" * 64
            entry.pop("auth_file_env")
            path = write_manifest(directory, manifest(agents=[entry]))
            with self.assertRaisesRegex(SystemExit, "auth_file_env"):
                run_measurement.load_manifest(path)

    def test_confirmatory_codex_agent_requires_frozen_reasoning_effort(self):
        with tempfile.TemporaryDirectory() as tmp:
            entry = {
                "name": "codex",
                "model": "gpt-test-2026-08-01",
                **frozen_price(),
                "container_image": "sha256:" + "a" * 64,
                "timeout_seconds": 900,
                "auth_file_env": "LOOP_CODEX_AUTH_FILE",
                "persist_refreshed_credentials": True,
            }
            data = manifest(
                apparatus_test=False,
                agents=[entry],
                candidate_sandbox_image="sha256:" + "b" * 64,
                artifact_archive_dir="artifacts",
            )
            path = write_manifest(Path(tmp), data)
            with self.assertRaisesRegex(SystemExit, "reasoning_effort"):
                run_measurement.load_manifest(path)

            entry["reasoning_effort"] = "medium"
            data.update(
                tasks=["s1_swebench", "s3", "g1", "b1"],
                cells=[cell.name for cell in run_measurement.se_experiment.CELLS],
                seeds=[11, 23, 37, 53, 71],
                cycles=6,
                agents=[
                    entry,
                    {
                        "name": "claude",
                        "adapter": "claude",
                        "model": "claude-test-2026-08-01",
                        **frozen_price(),
                        "container_image": "sha256:" + "c" * 64,
                        "timeout_seconds": 900,
                        "auth_file_env": "LOOP_CLAUDE_AUTH_FILE",
                        "persist_refreshed_credentials": True,
                    },
                ],
            )
            add_isolation_preflight(Path(tmp), data)
            path = write_manifest(Path(tmp), data)
            loaded = run_measurement.load_manifest(path)
            self.assertEqual(loaded["agents"][0]["reasoning_effort"], "medium")

    def test_confirmatory_design_shape_is_frozen(self):
        with tempfile.TemporaryDirectory() as tmp:
            agents = [
                {
                    "name": "codex",
                    "adapter": "codex",
                    "model": "gpt-test-2026-08-01",
                    "reasoning_effort": "medium",
                    **frozen_price(),
                    "container_image": "sha256:" + "a" * 64,
                    "timeout_seconds": 900,
                    "auth_file_env": "LOOP_CODEX_AUTH_FILE",
                    "persist_refreshed_credentials": True,
                },
                {
                    "name": "claude",
                    "adapter": "claude",
                    "model": "claude-test-2026-08-01",
                    **frozen_price(),
                    "container_image": "sha256:" + "c" * 64,
                    "timeout_seconds": 900,
                    "auth_file_env": "LOOP_CLAUDE_AUTH_FILE",
                    "persist_refreshed_credentials": True,
                },
            ]
            frozen = manifest(
                apparatus_test=False,
                tasks=["s1_swebench", "s3", "g1", "b1"],
                agents=agents,
                cells=[cell.name for cell in run_measurement.se_experiment.CELLS],
                seeds=[11, 23, 37, 53, 71],
                cycles=6,
                candidate_sandbox_image="sha256:" + "b" * 64,
                artifact_archive_dir="artifacts",
            )
            add_isolation_preflight(Path(tmp), frozen)
            self.assertEqual(run_measurement.load_manifest(write_manifest(Path(tmp), frozen)), frozen)

            short_quota_horizon = json.loads(json.dumps(frozen))
            short_quota_horizon["quota_wait_seconds"] = 300
            short_quota_horizon["quota_max_retries"] = 2
            with self.assertRaisesRegex(SystemExit, "at least seven days"):
                run_measurement.load_manifest(
                    write_manifest(Path(tmp), short_quota_horizon)
                )

            no_refresh_persistence = json.loads(json.dumps(frozen))
            no_refresh_persistence["agents"][1].pop("persist_refreshed_credentials")
            with self.assertRaisesRegex(SystemExit, "serialized OAuth"):
                run_measurement.load_manifest(
                    write_manifest(Path(tmp), no_refresh_persistence)
                )

            no_codex_refresh_persistence = json.loads(json.dumps(frozen))
            no_codex_refresh_persistence["agents"][0].pop(
                "persist_refreshed_credentials"
            )
            with self.assertRaisesRegex(SystemExit, "serialized OAuth"):
                run_measurement.load_manifest(
                    write_manifest(Path(tmp), no_codex_refresh_persistence)
                )

            external_claude_state = json.loads(json.dumps(frozen))
            external_claude_state["agents"][1]["state_file_env"] = "CLAUDE_STATE_FILE"
            with self.assertRaisesRegex(SystemExit, "external Claude state"):
                run_measurement.load_manifest(
                    write_manifest(Path(tmp), external_claude_state)
                )

            mutations = (
                {"tasks": ["s1_swebench", "s3", "g1"]},
                {"cells": ["grounded-numeric"]},
                {"seeds": [11, 23, 37, 53]},
                {"cycles": 5},
                {"agents": agents[:1]},
            )
            for override in mutations:
                path = write_manifest(Path(tmp), frozen | override)
                with self.assertRaises(SystemExit):
                    run_measurement.load_manifest(path)

            (Path(tmp) / frozen["isolation_preflight_record"]).write_text(
                "{}", encoding="utf-8"
            )
            with self.assertRaisesRegex(SystemExit, "digest"):
                run_measurement.load_manifest(write_manifest(Path(tmp), frozen))


class RunnerTest(unittest.TestCase):
    def test_serialized_subscription_agents_have_dedicated_worker_lanes(self):
        data = manifest(
            max_concurrent_agents=3,
            agents=[
                {
                    "name": "claude",
                    "adapter": "claude",
                    "persist_refreshed_credentials": True,
                },
                {
                    "name": "codex",
                    "adapter": "codex",
                    "persist_refreshed_credentials": True,
                },
            ],
        )
        self.assertEqual(
            run_measurement.worker_lane_limits(data),
            {"claude": 1, "codex": 1},
        )
        data["agents"][1].pop("persist_refreshed_credentials")
        self.assertEqual(
            run_measurement.worker_lane_limits(data),
            {"claude": 1, "other": 2},
        )
        data["max_concurrent_agents"] = 1
        self.assertEqual(run_measurement.worker_lane_limits(data), {"shared": 1})

    def test_shadow_price_uses_cache_and_request_level_long_context_tiers(self):
        agent = {
            **frozen_price(),
            "usd_per_1k_input": 1.0,
            "usd_per_1k_cached_input": 0.1,
            "usd_per_1k_output": 10.0,
            "long_context_threshold_input_tokens": 1000,
        }
        codex = run_measurement.price_of(agent, {
            "input_tokens": 1300,
            "uncached_input_tokens": 1060,
            "cached_input_tokens": 240,
            "cache_write_input_tokens": 0,
            "cache_write_5m_input_tokens": 0,
            "cache_write_1h_input_tokens": 0,
            "cache_write_input_tokens_exact": False,
            "output_tokens": 110,
            "request_usages": [
                {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 10},
                {"input_tokens": 1200, "cached_input_tokens": 200, "output_tokens": 100},
            ],
        })
        self.assertAlmostEqual(codex["lower_usd"], 3.704)
        self.assertAlmostEqual(codex["upper_usd"], 4.219)
        self.assertFalse(codex["exact"])
        self.assertEqual(codex["long_uncached_input_tokens"], 1000)

        claude = run_measurement.price_of(agent, {
            "input_tokens": 100,
            "uncached_input_tokens": 70,
            "cached_input_tokens": 30,
            "cache_write_input_tokens": 20,
            "cache_write_5m_input_tokens": 12,
            "cache_write_1h_input_tokens": 8,
            "cache_write_input_tokens_exact": True,
            "output_tokens": 10,
            "request_usages": [],
        })
        self.assertAlmostEqual(claude["lower_usd"], 0.184)
        self.assertEqual(claude["lower_usd"], claude["upper_usd"])
        self.assertTrue(claude["exact"])

    def test_shadow_price_refuses_unclassified_long_context_aggregate(self):
        agent = frozen_price() | {"long_context_threshold_input_tokens": 100}
        with self.assertRaisesRegex(RuntimeError, "without request telemetry"):
            run_measurement.price_of(agent, {
                "input_tokens": 101,
                "uncached_input_tokens": 101,
                "cached_input_tokens": 0,
                "cache_write_input_tokens": 0,
                "cache_write_5m_input_tokens": 0,
                "cache_write_1h_input_tokens": 0,
                "cache_write_input_tokens_exact": True,
                "output_tokens": 1,
                "request_usages": [],
            })

    def test_a_full_run_writes_one_record_per_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            data = manifest()
            path = write_manifest(directory, data)
            log = directory / "cycles.jsonl"
            result = run_cli(path, log, "run-a")
            self.assertEqual(result.returncode, 0, result.stderr)

            records = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
            expected = len(data["tasks"]) * len(data["cells"]) * len(data["seeds"]) * data["cycles"]
            self.assertEqual(len(records), expected)
            self.assertTrue(all(r["preregistration_commit"] == "0" * 40 for r in records))
            self.assertTrue(all(r["model_identity_matches"] for r in records))
            self.assertTrue(all(r["billing_mode"] == "subscription" for r in records))
            self.assertTrue(all(r["incremental_billed_usd"] == 0.0 for r in records))
            self.assertTrue(all(r["agent_completed"] for r in records))
            self.assertTrue(
                all(r["edit_success"] == r["candidate_changed"] for r in records)
            )
            self.assertTrue(all(r["judge_seconds"] == 0.0 for r in records))
            self.assertTrue(any(r["api_equivalent_usd"] > 0.0 for r in records))

    def test_resume_does_not_redo_completed_trajectories(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path = write_manifest(directory, manifest())
            log = directory / "cycles.jsonl"

            self.assertEqual(run_cli(path, log, "run-a").returncode, 0)
            first = len(log.read_text().splitlines())

            second = run_cli(path, log, "run-b")
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(
                len(log.read_text().splitlines()),
                first,
                "a resumed run repeated work that was already in the log",
            )
            plan, _ = json.JSONDecoder().raw_decode(second.stdout.lstrip())
            self.assertEqual(plan["trajectories_remaining"], 0)

    def test_an_abandoned_trajectory_is_rerun_from_the_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            data = manifest(seeds=[1], cells=["grounded-numeric"])
            path = write_manifest(directory, data)
            log = directory / "cycles.jsonl"

            # One cycle of a two-cycle trajectory, as a killed run would leave it.
            token = "s1|scripted|scripted-v1|grounded-numeric|1"
            log.write_text(json.dumps({"trajectory": token, "cycle": 1}) + "\n", encoding="utf-8")

            self.assertEqual(
                run_measurement.completed_trajectories(log, data["cycles"]),
                set(),
                "a partial trajectory was treated as complete",
            )
            result = run_cli(path, log, "run-c")
            self.assertEqual(result.returncode, 0, result.stderr)
            records = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
            fresh = [r for r in records if r.get("run_id") == "run-c"]
            self.assertEqual(len(fresh), data["cycles"])

    def test_attempts_are_not_merged_and_a_later_retry_can_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "cycles.jsonl"
            token = "s1|scripted|scripted-v1|grounded-numeric|1"
            rows = [
                {"trajectory": token, "attempt_id": "a", "cycle": 1},
                {"trajectory": token, "attempt_id": "a", "abandoned": True},
                {"trajectory": token, "attempt_id": "b", "cycle": 1},
                {"trajectory": token, "attempt_id": "b", "cycle": 2},
            ]
            log.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            self.assertEqual(run_measurement.completed_trajectories(log, 2), {token})

            rows[-1] = {"trajectory": token, "attempt_id": "c", "cycle": 2}
            log.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            self.assertEqual(run_measurement.completed_trajectories(log, 2), set())

    def test_the_run_stops_on_the_budget_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            # Ceiling below the estimate for the whole grid: refuse to start
            # rather than deliver a partial design.
            data = manifest(
                billing_mode="api",
                cost_ceiling_usd=0.005,
                estimated_api_equivalent_usd_per_trajectory=0.01,
            )
            path = write_manifest(directory, data)
            log = directory / "cycles.jsonl"
            result = run_cli(path, log, "run-d")
            self.assertEqual(result.returncode, 2)
            self.assertIn("ceiling", result.stderr)
            self.assertFalse(log.exists(), "a refused run still wrote records")

    def test_plan_only_reports_without_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path = write_manifest(directory, manifest())
            log = directory / "cycles.jsonl"
            result = run_cli(path, log, "run-e", "--plan-only")
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(result.stdout)
            self.assertEqual(plan["trajectories_total"], 4)
            self.assertEqual(plan["logical_cycle_rows"], 8)
            self.assertEqual(plan["unique_agent_executions"], 6)
            self.assertEqual(plan["max_concurrent_agents"], 3)
            self.assertEqual(plan["billing_mode"], "subscription")
            self.assertEqual(plan["estimated_incremental_billed_usd_remaining"], 0.0)
            self.assertFalse(log.exists())

    def test_quota_response_waits_and_retries_without_switching_billing(self):
        calls = []
        sleeps = []

        def flaky_driver(**kwargs):
            calls.append(kwargs["manifest"]["billing_mode"])
            if len(calls) == 1:
                raise run_measurement.QuotaLimitError("429", retry_after_seconds=2.5)
            return {"claim_improved": True, "input_tokens": 0, "output_tokens": 0}

        outcome = run_measurement.invoke_with_quota_wait(
            flaky_driver,
            manifest(),
            sleep_fn=sleeps.append,
        )
        self.assertEqual(calls, ["subscription", "subscription"])
        self.assertEqual(sleeps, [2.5])
        self.assertEqual(outcome["quota_wait_events"], 1)
        self.assertEqual(outcome["quota_wait_seconds"], 2.5)

    def test_structured_self_verdict_accepts_both_values_and_rejects_bad_schema(self):
        for improved in (True, False):
            outcome = run_measurement.normalize_self_report({
                "self_report": {
                    "improved": improved,
                    "confidence": 0.75,
                    "evidence": "public tests pass after the repair",
                }
            })
            self.assertTrue(outcome["claim_parsed"])
            self.assertIs(outcome["claim_improved"], improved)

        invalid = run_measurement.normalize_self_report({
            "self_report": {
                "improved": "yes",
                "confidence": 2.0,
                "evidence": "word " * 21,
            }
        })
        self.assertFalse(invalid["claim_parsed"])
        self.assertFalse(invalid["claim_improved"])

    def test_prior_billed_usage_is_restored_and_reservations_are_atomic(self):
        data = manifest(billing_mode="api", cost_ceiling_usd=1.0)
        digest = run_measurement.manifest_digest(data)
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "cycles.jsonl"
            log.write_text(
                json.dumps({
                    "manifest_digest": digest,
                    "api_equivalent_usd": 0.4,
                    "incremental_billed_usd": 0.4,
                })
                + "\n",
                encoding="utf-8",
            )
            shadow, billed = run_measurement.logged_costs(log, digest)
            budget = run_measurement.Budget("api", 1.0, shadow, billed)
            self.assertTrue(budget.reserve(0.6))
            self.assertFalse(budget.reserve(0.01))
            budget.release(0.6)
            self.assertTrue(budget.reserve(0.01))

    def test_post_invocation_oracle_failure_persists_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            data = manifest(
                seeds=[1],
                cells=["grounded-numeric"],
                cycles=1,
                max_concurrent_agents=1,
            )
            path = write_manifest(directory, data)
            log = directory / "cycles.jsonl"
            baseline = ({"score": 0.1, "metrics": {}, "valid": True}, 0.01)
            argv = [
                "run_measurement.py",
                "--manifest", str(path),
                "--log", str(log),
                "--run-id", "failure-run",
            ]
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(
                    run_measurement.se_experiment,
                    "run_oracle",
                    side_effect=[baseline, baseline, RuntimeError("oracle broke")],
                ),
                mock.patch("sys.stdout", new=io.StringIO()),
                mock.patch("sys.stderr", new=io.StringIO()),
            ):
                self.assertEqual(run_measurement.main(), 4)

            records = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(len(records), 1)
            failure = records[0]
            self.assertTrue(failure["abandoned"])
            self.assertGreater(failure["input_tokens"], 0)
            self.assertGreater(failure["api_equivalent_usd"], 0.0)
            self.assertEqual(failure["incremental_billed_usd"], 0.0)
            self.assertIn("oracle broke", failure["error"])

    def test_apparatus_run_records_unreported_model_without_claiming_confirmatory_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            data = manifest(
                agents=[{
                    "name": "unreported",
                    "model": "unreported-v1",
                    "usd_per_1k_input": 0.003,
                    "usd_per_1k_output": 0.015,
                }],
                seeds=[1],
                cells=["grounded-numeric"],
                cycles=1,
                max_concurrent_agents=1,
            )
            path = write_manifest(directory, data)
            log = directory / "cycles.jsonl"

            def unreported_driver(model, task, workspace, cycle, seed, manifest, feedback=""):
                return {
                    "claim_improved": False,
                    "self_report": {
                        "improved": False,
                        "confidence": 0.9,
                        "evidence": "apparatus-only model identity test",
                    },
                    "model_served": None,
                    "candidate_digest": run_measurement.digest_of(workspace),
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "agent_seconds": 0.01,
                }

            oracle = ({"score": 0.5, "metrics": {}, "valid": True}, 0.01)
            argv = [
                "run_measurement.py", "--manifest", str(path),
                "--log", str(log), "--run-id", "apparatus-unreported",
            ]
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.dict(run_measurement.DRIVERS, {"unreported": unreported_driver}),
                mock.patch.object(
                    run_measurement.se_experiment, "run_oracle", return_value=oracle
                ),
                mock.patch("sys.stdout", new=io.StringIO()),
                mock.patch("sys.stderr", new=io.StringIO()),
            ):
                self.assertEqual(run_measurement.main(), 0)

            record = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
            self.assertFalse(record["model_identity_matches"])
            self.assertEqual(record["model_identity_evidence"], "unreported")
            self.assertFalse(record["confirmatory_eligible"])
            self.assertFalse(replay.integrity([record], [], [])["clean"])

    def test_missing_served_model_is_an_integrity_failure(self):
        cycle = {
            "trajectory": "t",
            "model_identity_matches": False,
            "model_served": None,
        }
        report = replay.integrity([cycle], [], [])
        self.assertFalse(report["clean"])
        self.assertEqual(report["model_identity_mismatch_trajectories"], ["t"])

    def test_cycle_one_runs_once_and_branches_to_all_four_cells(self):
        calls = []
        lock = threading.Lock()

        def counting_driver(model, task, workspace, cycle, seed, manifest, feedback=""):
            with lock:
                calls.append((cycle, feedback))
            target = workspace / "versioning.py"
            target.write_text(target.read_text(encoding="utf-8") + f"\n# cycle {cycle}\n", encoding="utf-8")
            return {
                "claim_improved": cycle % 2 == 0,
                "self_report": {
                    "improved": cycle % 2 == 0,
                    "confidence": 0.8,
                    "evidence": "counting driver cycle verdict",
                },
                "judge_verdict": None,
                "model_served": model,
                "candidate_digest": run_measurement.digest_of(workspace),
                "input_tokens": 100,
                "output_tokens": 50,
                "agent_seconds": 0.01,
            }

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            data = manifest(
                agents=[{
                    "name": "counting",
                    "model": "counting-v1",
                    "usd_per_1k_input": 0.003,
                    "usd_per_1k_output": 0.015,
                }],
                cells=[cell.name for cell in run_measurement.se_experiment.CELLS],
                seeds=[7],
                cycles=2,
            )
            path = write_manifest(directory, data)
            log = directory / "cycles.jsonl"
            oracle = ({"score": 0.5, "metrics": {}, "valid": True}, 0.01)
            argv = [
                "run_measurement.py", "--manifest", str(path),
                "--log", str(log), "--run-id", "branch-run",
            ]
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.dict(run_measurement.DRIVERS, {"counting": counting_driver}),
                mock.patch.object(
                    run_measurement.se_experiment, "run_oracle", return_value=oracle
                ) as oracle_mock,
                mock.patch("sys.stdout", new=io.StringIO()),
                mock.patch("sys.stderr", new=io.StringIO()),
            ):
                self.assertEqual(run_measurement.main(), 0)

            self.assertEqual(sum(1 for cycle, _ in calls if cycle == 1), 1)
            self.assertEqual(sum(1 for cycle, _ in calls if cycle == 2), 4)
            self.assertEqual(oracle_mock.call_count, 12)
            records = [json.loads(line) for line in log.read_text().splitlines()]
            first = [record for record in records if record["cycle"] == 1]
            self.assertEqual(len(first), 4)
            self.assertEqual(len({record["attempt_id"] for record in records}), 1)
            self.assertEqual(len({record["shared_execution_id"] for record in first}), 1)
            self.assertEqual(len({record["candidate_digest"] for record in first}), 1)
            self.assertAlmostEqual(sum(record["cost_allocation_fraction"] for record in first), 1.0)
            self.assertEqual(sum(record["input_tokens"] for record in first), 100.0)
            self.assertEqual(sum(record["output_tokens"] for record in first), 50.0)
            self.assertAlmostEqual(sum(record["agent_seconds"] for record in first), 0.01)
            self.assertAlmostEqual(sum(record["oracle_seconds"] for record in first), 0.02)
            self.assertTrue(
                all(record["execution_oracle_seconds"] == 0.02 for record in first)
            )

    def test_frozen_schedule_runs_one_hash_ordered_cell_per_block_per_round(self):
        data = manifest(
            cells=[cell.name for cell in run_measurement.se_experiment.CELLS],
            seeds=[1, 2],
        )
        keys = run_measurement.trajectories(data)
        scheduled = run_measurement.scheduled_trajectories(keys, data)
        self.assertEqual(scheduled, run_measurement.scheduled_trajectories(keys, data))
        groups = {run_measurement.common_group(key) for key in keys}
        for round_index in range(4):
            round_keys = scheduled[
                round_index * len(groups):(round_index + 1) * len(groups)
            ]
            self.assertEqual(
                {run_measurement.common_group(key) for key in round_keys}, groups
            )
            self.assertTrue(
                all(
                    run_measurement.cell_order(data, key).index(key.cell)
                    == round_index
                    for key in round_keys
                )
            )

    def test_cycle_two_failure_abandons_the_whole_common_block(self):
        calls = []

        def driver(model, task, workspace, cycle, seed, manifest, feedback=""):
            calls.append(cycle)
            return {
                "claim_improved": False,
                "self_report": {
                    "improved": False,
                    "confidence": 0.8,
                    "evidence": "cycle two peer isolation test",
                },
                "model_served": model,
                "candidate_digest": run_measurement.digest_of(workspace),
                "input_tokens": 10,
                "output_tokens": 5,
                "agent_seconds": 0.01,
            }

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            cells = [cell.name for cell in run_measurement.se_experiment.CELLS]
            data = manifest(
                agents=[{
                    "name": "peer-test",
                    "model": "peer-test-v1",
                    "usd_per_1k_input": 0.003,
                    "usd_per_1k_output": 0.015,
                }],
                cells=cells,
                seeds=[1],
                cycles=2,
            )
            path = write_manifest(directory, data)
            log = directory / "cycles.jsonl"
            oracle = ({"score": 0.5, "metrics": {}, "valid": True}, 0.01)
            cycle_two_calls = 0
            cycle_two_lock = threading.Lock()

            def oracle_with_one_late_failure(*args, **kwargs):
                nonlocal cycle_two_calls
                candidate = Path(args[1])
                if candidate.name == "candidate-2":
                    with cycle_two_lock:
                        cycle_two_calls += 1
                        fail_this_call = cycle_two_calls == 1
                    if fail_this_call:
                        raise RuntimeError("one cycle-two oracle failed")
                return oracle

            argv = [
                "run_measurement.py", "--manifest", str(path),
                "--log", str(log), "--run-id", "late-failure",
            ]
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.dict(run_measurement.DRIVERS, {"peer-test": driver}),
                mock.patch.object(
                    run_measurement.se_experiment,
                    "run_oracle",
                    side_effect=oracle_with_one_late_failure,
                ),
                mock.patch("sys.stdout", new=io.StringIO()),
                mock.patch("sys.stderr", new=io.StringIO()),
            ):
                self.assertEqual(run_measurement.main(), 4)

            records = [json.loads(line) for line in log.read_text().splitlines()]
            abandoned = [record for record in records if record.get("abandoned")]
            self.assertEqual(len(abandoned), len(cells))
            self.assertNotIn("bundle_abandoned", abandoned[0])
            self.assertEqual(
                sum(bool(record.get("bundle_abandoned")) for record in abandoned),
                len(cells) - 1,
            )
            completed = {
                record["trajectory"]
                for record in records
                if record.get("cycle") == 2 and not record.get("abandoned")
            }
            self.assertEqual(len(completed), len(cells) - 1)
            self.assertEqual(
                run_measurement.completed_common_group_trajectories(
                    log,
                    data,
                    2,
                    run_measurement.manifest_digest(data),
                ),
                set(),
            )

    def test_hard_kill_partial_block_is_tombstoned_and_rerun_from_cycle_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            data = manifest(
                cells=[cell.name for cell in run_measurement.se_experiment.CELLS],
                seeds=[1],
                cycles=2,
            )
            path = write_manifest(directory, data)
            log = directory / "cycles.jsonl"
            key = run_measurement.trajectories(data)[0]
            digest = run_measurement.manifest_digest(data)
            partial = {
                "schema_version": run_measurement.SCHEMA_VERSION,
                "run_id": "killed-run",
                "attempt_id": "killed-run:attempt",
                "manifest_digest": digest,
                "preregistration_commit": data["preregistration_commit"],
                "trajectory": key.token(),
                **key.as_dict(),
                "cycle": 1,
            }
            log.write_text(json.dumps(partial) + "\n", encoding="utf-8")

            result = run_cli(path, log, "recovery-run")
            self.assertEqual(result.returncode, 0, result.stderr)
            records = [json.loads(line) for line in log.read_text().splitlines()]
            killed = [
                row for row in records
                if row.get("attempt_id") == "killed-run:attempt"
            ]
            self.assertTrue(any(row.get("reconciled_incomplete_common_group") for row in killed))
            complete = run_measurement.completed_common_group_trajectories(
                log, data, 2, digest
            )
            self.assertEqual(len(complete), 4)


class ArtifactArchiveTest(unittest.TestCase):
    def test_archive_is_content_addressed_and_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = root / "candidate"
            candidate.mkdir()
            (candidate / "source.py").write_text("value = 1\n", encoding="utf-8")
            (candidate / "nested").mkdir()
            (candidate / "nested" / "data.txt").write_text("observed\n", encoding="utf-8")
            archive = root / "archive"
            first = run_measurement.archive_candidate(candidate, archive)
            second = run_measurement.archive_candidate(candidate, archive)
            self.assertEqual(first, second)
            record = json.loads(
                (archive / "manifests" / f"{first}.json").read_text(encoding="utf-8")
            )
            files = {entry["path"]: entry for entry in record["entries"] if entry["type"] == "file"}
            self.assertEqual(files["source.py"]["size"], len("value = 1\n"))
            object_path = archive / "objects" / files["source.py"]["sha256"][:2] / files["source.py"]["sha256"]
            self.assertEqual(object_path.read_text(encoding="utf-8"), "value = 1\n")
            cycle = {
                "schema_version": 3,
                "trajectory": "archive-test",
                "attempt_id": "complete",
                "cycle": 1,
                "cycles_planned": 1,
                "oracle_delta": 0.0,
                "apparatus_test": False,
                "model_served": "model-v1",
                "model_identity_matches": True,
                "manifest_digest": "manifest",
                "preregistration_commit": "freeze",
                "candidate_archive_manifest_sha256": first,
            }
            missing_root = replay.integrity(
                [cycle], [], [], require_archive_files=True
            )
            self.assertTrue(missing_root["candidate_archive_root_missing"])
            self.assertFalse(missing_root["clean"])

            verified = replay.integrity(
                [cycle],
                [],
                [],
                archive_root=archive,
                require_archive_files=True,
            )
            self.assertTrue(verified["candidate_archive_files_verified"])
            self.assertEqual(verified["candidate_archive_manifests_verified"], 1)
            self.assertTrue(verified["clean"])

            noncanonical_payload = json.dumps(record, indent=2).encode()
            noncanonical_id = hashlib.sha256(noncanonical_payload).hexdigest()
            (archive / "manifests" / f"{noncanonical_id}.json").write_bytes(
                noncanonical_payload + b"\n"
            )
            self.assertEqual(
                replay.verify_candidate_archive(archive, noncanonical_id),
                "manifest JSON is not canonical",
            )

            object_path.unlink()
            corrupted = replay.integrity(
                [cycle],
                [],
                [],
                archive_root=archive,
                require_archive_files=True,
            )
            self.assertEqual(
                corrupted["invalid_candidate_archive_trajectories"],
                ["archive-test"],
            )
            self.assertFalse(corrupted["clean"])


class ReplayTest(unittest.TestCase):
    def test_empty_replay_fails_closed(self):
        report = replay.integrity([], [], [])
        self.assertTrue(report["no_completed_cycle_records"])
        self.assertFalse(report["clean"])

    def test_every_reported_number_comes_from_the_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path = write_manifest(directory, manifest())
            log = directory / "cycles.jsonl"
            self.assertEqual(run_cli(path, log, "run-a").returncode, 0)

            cycles, abandoned, unparsable = replay.load(log)
            grouped = replay.group_trajectories(cycles)
            report = replay.by_cell(grouped)
            self.assertEqual(len(grouped), 4)
            self.assertTrue(replay.integrity(cycles, abandoned, unparsable)["clean"])
            for key, cell in report.items():
                self.assertIsNotNone(cell["delivered_gain_median"], key)
                self.assertGreater(cell["api_equivalent_usd"], 0.0, key)
                self.assertEqual(cell["incremental_billed_usd"], 0.0, key)
                self.assertIsNone(cell["gain_per_incremental_billed_usd"], key)
                self.assertTrue(cell["outcome_is_hob"], key)
                self.assertEqual(
                    cell["mirage_rate_is_structural"],
                    key.split("|")[-1].startswith("grounded"),
                    key,
                )

    def test_a_grounded_cell_gates_only_on_hoa(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path = write_manifest(directory, manifest())
            log = directory / "cycles.jsonl"
            result = run_cli(path, log, "run-a")
            self.assertEqual(result.returncode, 0, result.stderr)
            cycles, _, _ = replay.load(log)
            grounded = [r for r in cycles if r["cell_gate_grounded"] and r["accepted"]]
            self.assertTrue(grounded)
            self.assertTrue(
                all(r["delta_hoa"] > 0 for r in grounded),
                "a grounded gate accepted a non-positive HO-A delta",
            )
            self.assertTrue(all("delta_hob" in r for r in grounded))

    def test_integrity_problems_are_reported_not_averaged(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "cycles.jsonl"
            log.write_text(
                "\n".join(
                    [
                        json.dumps({"trajectory": "t1", "cycle": 1, "canary_leak": True,
                                    "accepted": True, "oracle_delta": 1.0}),
                        json.dumps({"trajectory": "t2", "abandoned": True}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            cycles, abandoned, unparsable = replay.load(log)
            report = replay.integrity(cycles, abandoned, unparsable)
            self.assertFalse(report["clean"])
            self.assertEqual(report["canary_leak_trajectories"], ["t1"])
            self.assertEqual(report["abandoned_trajectories"], ["t2"])

    def test_missing_trajectory_rows_are_integrity_defects_not_crashes(self):
        cycles = [{"schema_version": 2, "cycle": 1, "oracle_delta": 0.0}]
        grouped = replay.group_trajectories(cycles)
        report = replay.integrity(cycles, [], [])
        self.assertEqual(grouped, {})
        self.assertEqual(report["missing_trajectory_records"], 1)
        self.assertFalse(report["clean"])

    def test_unparsable_and_ungraded_records_are_not_measurements(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "cycles.jsonl"
            log.write_text(
                json.dumps({
                    "trajectory": "t1",
                    "attempt_id": "a",
                    "cycle": 1,
                    "accepted": True,
                    "oracle_delta": None,
                })
                + "\n{truncated\n",
                encoding="utf-8",
            )
            cycles, abandoned, unparsable = replay.load(log)
            report = replay.integrity(cycles, abandoned, unparsable)
            metrics = replay.trajectory_metrics(cycles)
            self.assertEqual(unparsable, [2])
            self.assertFalse(report["clean"])
            self.assertEqual(metrics["ungraded_cycles"], 1)
            self.assertIsNone(metrics["mirage_rate"])

    def test_replay_reports_registered_fixed_denominator_incidences(self):
        cycles = [
            {
                "cycle": 1,
                "cycles_planned": 6,
                "oracle_delta": -1.0,
                "accepted": True,
            },
            {
                "cycle": 2,
                "cycles_planned": 6,
                "oracle_delta": 1.0,
                "accepted": False,
            },
        ]
        metrics = replay.trajectory_metrics(cycles)
        self.assertAlmostEqual(metrics["harmful_acceptance_incidence"], 1 / 6, places=6)
        self.assertAlmostEqual(metrics["false_rejection_incidence"], 1 / 6, places=6)

    def test_gate_mirage_and_outcome_regression_use_different_halves(self):
        cycles = [
            {
                "cycle": 1,
                "cycles_planned": 6,
                "oracle_delta": 0.0,
                "delta_hoa": 0.1,
                "delta_hob": 0.0,
                "accepted": True,
            },
            {
                "cycle": 2,
                "cycles_planned": 6,
                "oracle_delta": 0.2,
                "delta_hoa": -0.1,
                "delta_hob": 0.2,
                "accepted": False,
            },
        ]
        metrics = replay.trajectory_metrics(cycles)
        self.assertEqual(metrics["mirage_rate"], 0.0)
        self.assertEqual(metrics["regression_acceptance_rate"], 1.0)
        self.assertAlmostEqual(metrics["harmful_acceptance_incidence"], 1 / 6, places=6)
        self.assertAlmostEqual(metrics["false_rejection_incidence"], 1 / 6, places=6)

    def test_schema_three_confirmatory_row_without_archive_is_unclean(self):
        cycles = [
            {
                "schema_version": 3,
                "trajectory": "t1",
                "attempt_id": "a",
                "cycle": 1,
                "cycles_planned": 1,
                "oracle_delta": 0.0,
                "apparatus_test": False,
                "model_served": "model-v1",
                "model_identity_matches": True,
                "manifest_digest": "m",
                "preregistration_commit": "p",
            }
        ]
        report = replay.integrity(cycles, [], [])
        self.assertEqual(report["missing_candidate_archive_trajectories"], ["t1"])
        self.assertFalse(report["clean"])

    def test_schema_four_confirmatory_row_without_allocation_contract_is_unclean(self):
        cycles = [
            {
                "schema_version": 4,
                "trajectory": "t1",
                "attempt_id": "a",
                "cycle": 1,
                "cycles_planned": 1,
                "oracle_delta": 0.0,
                "apparatus_test": False,
                "model_served": "model-v1",
                "model_identity_matches": True,
                "manifest_digest": "m",
                "preregistration_commit": "p",
                "candidate_archive_manifest_sha256": "archive",
            }
        ]
        report = replay.integrity(cycles, [], [])
        self.assertEqual(report["invalid_measurement_contract_trajectories"], ["t1"])
        self.assertFalse(report["clean"])

    def test_schema_four_allocation_contract_is_replay_clean(self):
        cycles = [
            {
                "schema_version": 4,
                "trajectory": "t1",
                "attempt_id": "a",
                "cycle": 1,
                "cycles_planned": 1,
                "oracle_delta": 0.0,
                "apparatus_test": False,
                "model_served": "model-v1",
                "model_identity_matches": True,
                "manifest_digest": "m",
                "preregistration_commit": "p",
                "candidate_archive_manifest_sha256": "archive",
                "cost_allocation_fraction": 0.25,
                "candidate_changed": True,
                "agent_completed": True,
                "edit_success": True,
                "execution_input_tokens": 100,
                "execution_output_tokens": 20,
                "execution_agent_seconds": 4.0,
                "execution_oracle_seconds": 2.0,
                "execution_api_equivalent_usd": 1.0,
                "input_tokens": 25.0,
                "output_tokens": 5.0,
                "agent_seconds": 1.0,
                "oracle_seconds": 0.5,
                "judge_seconds": 0.0,
                "api_equivalent_usd": 0.25,
            }
        ]
        report = replay.integrity(cycles, [], [])
        self.assertEqual(report["invalid_measurement_contract_trajectories"], [])
        self.assertTrue(report["clean"])

    def test_schema_five_shadow_price_is_recomputed_from_the_log(self):
        schedule = run_measurement.price_schedule(frozen_price())
        row = {
            "schema_version": 5,
            "trajectory": "t1",
            "attempt_id": "a",
            "cycle": 1,
            "cycles_planned": 1,
            "oracle_delta": 0.0,
            "apparatus_test": False,
            "cell_schedule_seed": "replay-test-order-v1",
            "cell_schedule_position": 1,
            "model_served": "model-v1",
            "model_identity_matches": True,
            "manifest_digest": "m",
            "preregistration_commit": "p",
            "candidate_archive_manifest_sha256": "archive",
            "credential_leak_scan_passed": True,
            "cost_allocation_fraction": 0.25,
            "candidate_changed": True,
            "agent_completed": True,
            "edit_success": True,
            "execution_input_tokens": 100,
            "execution_uncached_input_tokens": 80,
            "execution_cached_input_tokens": 20,
            "execution_cache_write_input_tokens": 0,
            "execution_cache_write_5m_input_tokens": 0,
            "execution_cache_write_1h_input_tokens": 0,
            "execution_standard_uncached_input_tokens": 80,
            "execution_standard_cached_input_tokens": 20,
            "execution_standard_output_tokens": 20,
            "execution_long_uncached_input_tokens": 0,
            "execution_long_cached_input_tokens": 0,
            "execution_long_output_tokens": 0,
            "execution_output_tokens": 20,
            "execution_agent_seconds": 4.0,
            "execution_oracle_seconds": 2.0,
            "execution_api_equivalent_usd_lower_bound": 0.0122,
            "execution_api_equivalent_usd": 0.0142,
            "input_tokens": 25.0,
            "uncached_input_tokens": 20.0,
            "cached_input_tokens": 5.0,
            "cache_write_input_tokens": 0.0,
            "cache_write_5m_input_tokens": 0.0,
            "cache_write_1h_input_tokens": 0.0,
            "output_tokens": 5.0,
            "agent_seconds": 1.0,
            "oracle_seconds": 0.5,
            "judge_seconds": 0.0,
            "api_equivalent_usd_lower_bound": 0.00305,
            "api_equivalent_usd": 0.00355,
            "request_usage_count": 1,
            "cache_write_input_tokens_exact": False,
            "api_equivalent_price_exact": False,
            "shadow_price_schedule": schedule,
        }
        report = replay.integrity([row], [], [])
        self.assertTrue(report["clean"])

        schema_six = json.loads(json.dumps(row))
        schema_six["schema_version"] = 6
        missing_publication = replay.integrity([schema_six], [], [])
        self.assertEqual(
            missing_publication["invalid_measurement_contract_trajectories"],
            ["t1"],
        )
        schema_six.update(
            {
                "external_preregistration_doi": "10.5281/zenodo.12345678",
                "external_preregistration_record_id": "12345678",
                "external_preregistration_evidence_sha256": "a" * 64,
                "external_preregistration_bundle_sha256": "b" * 64,
                "external_preregistration_verified_utc": "2026-08-13T00:00:00Z",
                "wall_clock_utc": "2026-08-13T00:00:01Z",
            }
        )
        valid_publication = replay.integrity([schema_six], [], [])
        self.assertTrue(valid_publication["clean"])
        self.assertFalse(
            valid_publication[
                "mixed_or_missing_external_preregistration_publication"
            ]
        )
        schema_six["wall_clock_utc"] = "2026-08-12T23:59:59Z"
        prepublication_row = replay.integrity([schema_six], [], [])
        self.assertEqual(
            prepublication_row["invalid_measurement_contract_trajectories"],
            ["t1"],
        )

        row["credential_leak_scan_passed"] = False
        report = replay.integrity([row], [], [])
        self.assertEqual(report["invalid_measurement_contract_trajectories"], ["t1"])
        self.assertFalse(report["clean"])
        row["credential_leak_scan_passed"] = True

        row["execution_api_equivalent_usd"] = 0.0143
        report = replay.integrity([row], [], [])
        self.assertEqual(report["invalid_measurement_contract_trajectories"], ["t1"])
        self.assertFalse(report["clean"])

    def test_abandoned_attempt_rows_are_excluded_from_cell_statistics(self):
        cycles = [
            {
                "trajectory": "t1", "attempt_id": "failed", "cycle": 1,
                "task": "s1", "agent": "codex", "cell": "grounded-numeric",
                "oracle_delta": -1.0, "accepted": True,
            },
            {
                "trajectory": "t1", "attempt_id": "retry", "cycle": 1,
                "task": "s1", "agent": "codex", "cell": "grounded-numeric",
                "oracle_delta": 1.0, "accepted": True,
            },
        ]
        abandoned = [{"trajectory": "t1", "attempt_id": "failed", "abandoned": True}]
        grouped = replay.group_trajectories(cycles, abandoned)
        self.assertEqual(list(grouped), ["t1|attempt=retry"])

    def test_a_complete_retry_recovers_an_abandoned_attempt(self):
        cycles = [
            {
                "trajectory": "t1", "attempt_id": "retry", "cycle": cycle,
                "cycles_planned": 2, "oracle_delta": 1.0,
            }
            for cycle in (1, 2)
        ]
        abandoned = [{"trajectory": "t1", "attempt_id": "failed", "abandoned": True}]
        report = replay.integrity(cycles, abandoned, [])
        self.assertTrue(report["clean"])
        self.assertEqual(report["recovered_abandoned_trajectories"], ["t1"])


if __name__ == "__main__":
    unittest.main()
