#!/usr/bin/env python3
"""Adversarial and scale tests for the repository-scale S1 oracle."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import se_experiment


ROOT = Path(__file__).resolve().parent
TASK = ROOT / "se_tasks" / "s1_swebench"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MATERIALIZE = load("s1_materialize", TASK / "materialize.py")
ORACLE = load("s1_oracle", TASK / "oracle.py")
SELECTION = load("s1_selection", TASK / "verify_selection.py")


class RepositoryScaleOracleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="loop-s1-test-")
        cls.root = Path(cls.temporary.name)
        cls.seed = cls.root / "seed"
        MATERIALIZE.materialize(cls.seed)
        cls.row = ORACLE.instance_row()

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def candidate(self, name: str) -> Path:
        destination = self.root / name
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(self.seed, destination, symlinks=True)
        return destination

    def test_seed_is_a_real_repository_and_hidden_patch_is_absent(self):
        python_files = list(self.seed.rglob("*.py"))
        self.assertGreater(len(python_files), 1000)
        self.assertFalse((self.seed / ".git").exists())
        self.assertNotIn(
            ORACLE.CONFIG["test_patch_sha256"],
            "".join(path.read_text(encoding="utf-8", errors="ignore") for path in (
                self.seed / "ISSUE.md", self.seed / "PUBLIC_TESTS.md"
            )),
        )

    def test_registered_instance_is_selected_from_the_public_frame(self):
        result = SELECTION.select()
        self.assertEqual(result["screening_frame_count"], 261)
        self.assertEqual(result["eligible_count"], 2)
        self.assertEqual(result["selected"]["instance_id"], ORACLE.CONFIG["instance_id"])

    def test_task_uses_the_shared_runner_contract(self):
        result_a, _ = se_experiment.run_oracle("s1_swebench", self.seed, "a", 11)
        result_b, _ = se_experiment.run_oracle("s1_swebench", self.seed, "b", 11)
        self.assertTrue(result_a["valid"] and result_b["valid"])
        self.assertEqual(result_a["score"], 0.5)
        self.assertEqual(result_b["score"], 0.5)

    def test_hoa_and_hob_are_disjoint_and_complete(self):
        for kind, cases in (
            ("f2p", self.row["FAIL_TO_PASS"]),
            ("p2p", self.row["PASS_TO_PASS"]),
        ):
            left = ORACLE.split_cases(cases, kind, 17, "a")
            right = ORACLE.split_cases(cases, kind, 17, "b")
            self.assertFalse(set(left) & set(right))
            self.assertEqual(set(left) | set(right), set(cases))

    def test_null_seed_reference_ordering_on_both_halves(self):
        seed = self.candidate("baseline")
        broken = self.candidate("broken")
        reference = self.candidate("reference")
        serializer_init = broken / "django" / "core" / "serializers" / "__init__.py"
        serializer_init.write_text(
            serializer_init.read_text(encoding="utf-8")
            + "\n\ndef get_serializer(*args, **kwargs):\n"
              "    raise RuntimeError('destructive null candidate')\n",
            encoding="utf-8",
        )
        subprocess.run(
            ["patch", "-p1", "--batch"],
            cwd=reference,
            input=self.row["patch"],
            text=True,
            check=True,
            capture_output=True,
        )
        for half in ("a", "b"):
            with self.subTest(half=half):
                null_score = ORACLE.score(broken, half, 17)["score"]
                seed_score = ORACLE.score(seed, half, 17)["score"]
                reference_score = ORACLE.score(reference, half, 17)["score"]
                self.assertLess(null_score, seed_score)
                self.assertLess(seed_score, reference_score)
                self.assertEqual(reference_score, 1.0)

    def test_test_modification_is_rejected(self):
        candidate = self.candidate("test-tamper")
        target = candidate / "tests" / "serializers" / "tests.py"
        target.write_text(target.read_text(encoding="utf-8") + "\n# tamper\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "reward-hacking guard"):
            ORACLE.score(candidate, "a", 23)

    def test_candidate_git_metadata_is_rejected_before_evaluation(self):
        candidate = self.candidate("git-metadata")
        metadata = candidate / ".git"
        metadata.mkdir()
        (metadata / "config").write_text("[filter \"attack\"]\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Git metadata"):
            ORACLE.validate_candidate(candidate)

    def test_internal_candidate_symlink_is_rejected_before_evaluation(self):
        candidate = self.candidate("internal-symlink")
        (candidate / "serializer-link.py").symlink_to(
            "django/core/serializers/__init__.py"
        )
        with self.assertRaisesRegex(ValueError, "symlink"):
            ORACLE.validate_candidate(candidate)

    def test_candidate_gitattributes_is_rejected_before_evaluation(self):
        candidate = self.candidate("git-attributes")
        (candidate / ".gitattributes").write_text(
            "*.py filter=attack\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "Git attributes"):
            ORACLE.validate_candidate(candidate)


if __name__ == "__main__":
    unittest.main(verbosity=2)
