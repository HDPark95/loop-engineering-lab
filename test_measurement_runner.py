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
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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
            plan = json.loads(second.stdout.split("\n{")[0] if second.stdout.startswith("{") else second.stdout)
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
            return {"claim_improved": True}

        outcome = run_measurement.invoke_with_quota_wait(
            flaky_driver,
            manifest(),
            sleep_fn=sleeps.append,
        )
        self.assertEqual(calls, ["subscription", "subscription"])
        self.assertEqual(sleeps, [2.5])
        self.assertEqual(outcome["quota_wait_events"], 1)
        self.assertEqual(outcome["quota_wait_seconds"], 2.5)


class ReplayTest(unittest.TestCase):
    def test_every_reported_number_comes_from_the_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path = write_manifest(directory, manifest())
            log = directory / "cycles.jsonl"
            self.assertEqual(run_cli(path, log, "run-a").returncode, 0)

            cycles, abandoned = replay.load(log)
            grouped = replay.group_trajectories(cycles)
            report = replay.by_cell(grouped)
            self.assertEqual(len(grouped), 4)
            self.assertTrue(replay.integrity(cycles, abandoned)["clean"])
            for key, cell in report.items():
                self.assertIsNotNone(cell["delivered_gain_median"], key)
                self.assertGreater(cell["api_equivalent_usd"], 0.0, key)
                self.assertEqual(cell["incremental_billed_usd"], 0.0, key)
                self.assertIsNone(cell["gain_per_incremental_billed_usd"], key)
                # "ungrounded" contains "grounded", so the cell name has to be
                # split out rather than matched as a substring.
                cell_name = key.split("|")[-1]
                self.assertEqual(
                    cell["mirage_rate_is_structural"], cell_name.startswith("grounded"), key
                )

    def test_a_grounded_cell_never_shows_a_mirage(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path = write_manifest(directory, manifest())
            log = directory / "cycles.jsonl"
            run_cli(path, log, "run-a")
            cycles, _ = replay.load(log)
            grounded = [r for r in cycles if r["cell_gate_grounded"] and r["accepted"]]
            self.assertTrue(grounded)
            self.assertTrue(
                all(r["oracle_delta"] > 0 for r in grounded),
                "a grounded gate accepted a non-positive delta, which the gate rule forbids",
            )

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
            cycles, abandoned = replay.load(log)
            report = replay.integrity(cycles, abandoned)
            self.assertFalse(report["clean"])
            self.assertEqual(report["canary_leak_trajectories"], ["t1"])
            self.assertEqual(report["abandoned_trajectories"], ["t2"])


if __name__ == "__main__":
    unittest.main()
