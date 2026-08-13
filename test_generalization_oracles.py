#!/usr/bin/env python3
"""Ordering, split, and observation tests for G1 and B1."""

from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import se_experiment


ROOT = Path(__file__).resolve().parent


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


G1 = load("g1_oracle", ROOT / "se_tasks" / "g1_generalization" / "oracle.py")
B1 = load("b1_oracle", ROOT / "se_tasks" / "b1_boundary" / "oracle.py")


class OracleFixture(unittest.TestCase):
    task_name = ""
    oracle = None

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix=f"loop-{self.task_name}-")
        self.root = Path(self.temporary.name)
        self.seed = self.root / "seed"
        shutil.copytree(
            ROOT / "se_tasks" / self.task_name / "seed", self.seed, symlinks=True
        )

    def tearDown(self):
        self.temporary.cleanup()

    def candidate(self, name: str) -> Path:
        path = self.root / name
        shutil.copytree(self.seed, path)
        return path


class G1OracleTest(OracleFixture):
    task_name = "g1_generalization"
    oracle = G1

    def test_task_uses_the_shared_runner_contract(self):
        result, _ = se_experiment.run_oracle("g1", self.seed, "a", 11)
        self.assertTrue(result["valid"])

    def test_null_seed_reference_ordering_for_all_registered_seeds(self):
        null = self.candidate("null")
        (null / "landing.html").write_text("<html><body></body></html>\n", encoding="utf-8")
        (null / "funnel.json").write_text('{"steps": []}\n', encoding="utf-8")
        (null / "copy.txt").write_text("\n", encoding="utf-8")
        reference = self.candidate("reference")
        (reference / "landing.html").write_text(
            "<html lang='en'><body><h1>Create an account</h1>"
            "<form><input type='email' name='email' required>"
            "<button type='submit'>Sign up now</button></form></body></html>\n",
            encoding="utf-8",
        )
        (reference / "funnel.json").write_text(
            '{"steps": ["landing", "email", "confirm"]}\n', encoding="utf-8"
        )
        (reference / "copy.txt").write_text(
            " ".join(["clear"] * 65) + "\n", encoding="utf-8"
        )
        for seed in range(5):
            for half in ("a", "b"):
                with self.subTest(seed=seed, half=half):
                    null_score = G1.score(null, half, seed)["score"]
                    seed_score = G1.score(self.seed, half, seed)["score"]
                    reference_score = G1.score(reference, half, seed)["score"]
                    self.assertLess(null_score, seed_score)
                    self.assertLess(seed_score, reference_score)

    def test_score_ignores_candidate_reported_numbers(self):
        candidate = self.candidate("reported")
        (candidate / "result.json").write_text(
            json.dumps({"score": 1.0, "completed_signups": 2000}), encoding="utf-8"
        )
        self.assertEqual(
            G1.score(candidate, "a", 1)["score"],
            G1.score(self.seed, "a", 1)["score"],
        )


class B1OracleTest(OracleFixture):
    task_name = "b1_boundary"
    oracle = B1

    def test_task_uses_the_shared_runner_contract(self):
        result, _ = se_experiment.run_oracle("b1", self.seed, "b", 11)
        self.assertTrue(result["valid"])

    def test_requirement_halves_are_disjoint_and_complete(self):
        names = list(B1.requirements(self.seed))
        left = B1.selected(names, 9, "a")
        right = B1.selected(names, 9, "b")
        self.assertFalse(set(left) & set(right))
        self.assertEqual(set(left) | set(right), set(names))

    def test_null_seed_reference_ordering_for_all_registered_seeds(self):
        null = self.candidate("null")
        (null / "landing.html").write_text("\n", encoding="utf-8")
        (null / "funnel.json").write_text('{"steps": []}\n', encoding="utf-8")
        (null / "copy.txt").write_text("1% best\n", encoding="utf-8")
        reference = self.candidate("reference")
        (reference / "landing.html").write_text(
            "<!doctype html><html lang='en'><head><title>Signup</title></head><body>"
            "<h1>Create your account</h1><form method='post' action='/signup'>"
            "<label for='email'>Email address</label>"
            "<input id='email' type='email' name='email' required autocomplete='email'>"
            "<button type='submit'>Create account</button></form>"
            "<a href='/privacy'>Privacy information</a></body></html>\n",
            encoding="utf-8",
        )
        (reference / "funnel.json").write_text(
            '{"steps": ["landing", "email", "confirm"]}\n', encoding="utf-8"
        )
        reference_copy = (
            "Create an account to use the service and manage your preferences. "
            "We respect your privacy and explain how account information is handled. "
            "Messages are optional, and you may unsubscribe at any time from settings. "
            "Review the signup details, enter your email address, and confirm when ready. "
            "Support remains available if you have questions about registration or data."
        )
        (reference / "copy.txt").write_text(reference_copy + "\n", encoding="utf-8")
        self.assertTrue(50 <= len(reference_copy.split()) <= 90)
        for seed in range(5):
            for half in ("a", "b"):
                with self.subTest(seed=seed, half=half):
                    null_score = B1.score(null, half, seed)["score"]
                    seed_score = B1.score(self.seed, half, seed)["score"]
                    reference_score = B1.score(reference, half, seed)["score"]
                    self.assertLess(null_score, seed_score)
                    self.assertLess(seed_score, reference_score)
                    self.assertEqual(reference_score, 1.0)

    def test_score_ignores_candidate_reported_numbers(self):
        candidate = self.candidate("reported")
        (candidate / "result.json").write_text('{"requirements_passed": 22}\n', encoding="utf-8")
        self.assertEqual(
            B1.score(candidate, "b", 2)["score"],
            B1.score(self.seed, "b", 2)["score"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
