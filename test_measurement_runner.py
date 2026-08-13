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
        "quota_wait_seconds": 60,
        "quota_max_retries": 4,
        "estimated_api_equivalent_usd_per_trajectory": 0.01,
        "preregistration_commit": "0" * 40,
    }
    base.update(overrides)
    return base


def write_manifest(directory: Path, data: dict) -> Path:
    path = directory / "manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


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
                "usd_per_1k_input": 0.1,
                "usd_per_1k_output": 0.2,
                "container_image": "agent-image:latest",
                "timeout_seconds": 900,
                "auth_file_env": "LOOP_CODEX_AUTH_FILE",
            }
            path = write_manifest(directory, manifest(agents=[entry]))
            with self.assertRaisesRegex(SystemExit, "sha256"):
                run_measurement.load_manifest(path)

            entry["container_image"] = "agent-image@sha256:" + "a" * 64
            entry.pop("auth_file_env")
            path = write_manifest(directory, manifest(agents=[entry]))
            with self.assertRaisesRegex(SystemExit, "auth_file_env"):
                run_measurement.load_manifest(path)


class RunnerTest(unittest.TestCase):
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

    def test_cycle_two_failure_does_not_abandon_peer_trajectories(self):
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

            def oracle_with_one_late_failure(*args, **kwargs):
                nonlocal cycle_two_calls
                candidate = Path(args[1])
                if candidate.name == "candidate-2":
                    cycle_two_calls += 1
                    if cycle_two_calls == 1:
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
            self.assertEqual(len(abandoned), 1)
            self.assertNotIn("bundle_abandoned", abandoned[0])
            completed = {
                record["trajectory"]
                for record in records
                if record.get("cycle") == 2 and not record.get("abandoned")
            }
            self.assertEqual(len(completed), len(cells) - 1)


class ReplayTest(unittest.TestCase):
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
                self.assertFalse(cell["mirage_rate_is_structural"], key)

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
