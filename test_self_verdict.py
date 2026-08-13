"""The self-report must come from the agent, and an unreadable one must reject.

Before 2026-08-10 the in-band self-report was computed as `edited and ok`: the
artifact hash changed and the agent process exited cleanly. That is edit
success. The pilot's headline observation, that the agent claimed improvement in
all 54 cycles, was an arithmetic consequence of that definition rather than
anything the agent said. The variable now comes from a structured verdict the
agent is asked for, and these tests pin the two properties the measurement
depends on: the verdict is read from the agent's own words, and silence is a
reject rather than an accept.
"""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("run_pilot", ROOT / "run_pilot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run_pilot = _load()
parse = run_pilot.parse_self_verdict


class SelfVerdictIsTheAgentsOwn(unittest.TestCase):
    def test_true_verdict_is_read(self):
        improved, confidence, evidence, parsed = parse(
            'I shortened the funnel.\n'
            '{"improved": true, "confidence": 0.8, "evidence": "fewer steps"}'
        )
        self.assertTrue(improved)
        self.assertTrue(parsed)
        self.assertEqual(confidence, 0.8)
        self.assertEqual(evidence, "fewer steps")

    def test_false_verdict_is_read(self):
        # The point of eliciting the verdict is that it can be false. If this
        # test ever becomes unreachable in a real run, the elicitation failed.
        improved, _, _, parsed = parse(
            '{"improved": false, "confidence": 0.2, "evidence": "tests still fail"}'
        )
        self.assertFalse(improved)
        self.assertTrue(parsed)

    def test_code_fence_does_not_hide_it(self):
        improved, _, _, parsed = parse(
            '```json\n{"improved": true, "confidence": 0.8, "evidence": "tests pass"}\n```'
        )
        self.assertTrue(improved)
        self.assertTrue(parsed)

    def test_only_the_final_line_is_a_verdict(self):
        improved, _, _, parsed = parse(
            '{"improved": true, "confidence": 0.8, "evidence": "early guess"}\n'
            'explanation after the object'
        )
        self.assertFalse(improved)
        self.assertFalse(parsed)


class SilenceIsAReject(unittest.TestCase):
    def test_prose_without_a_verdict_does_not_accept(self):
        improved, _, _, parsed = parse("I edited landing.html and it looks better.")
        self.assertFalse(improved)
        self.assertFalse(parsed)

    def test_empty_output_does_not_accept(self):
        improved, _, _, parsed = parse("")
        self.assertFalse(improved)
        self.assertFalse(parsed)

    def test_json_without_the_field_does_not_accept(self):
        improved, _, _, parsed = parse('{"reason": "did stuff"}')
        self.assertFalse(improved)
        self.assertFalse(parsed)

    def test_invalid_field_types_reject(self):
        invalid = (
            '{"improved": "false", "confidence": 0.8, "evidence": "typed wrong"}',
            '{"improved": true, "confidence": "high", "evidence": "typed wrong"}',
            '{"improved": true, "confidence": 1.2, "evidence": "out of range"}',
            '{"improved": true, "confidence": 0.8, "evidence": ""}',
        )
        for text in invalid:
            with self.subTest(text=text):
                improved, confidence, _, parsed = parse(text)
                self.assertFalse(improved)
                self.assertFalse(parsed)
                self.assertIsNone(confidence)

    def test_evidence_is_limited_to_twenty_words(self):
        evidence = " ".join(["word"] * 21)
        improved, _, _, parsed = parse(
            json.dumps({"improved": True, "confidence": 0.8, "evidence": evidence})
        )
        self.assertFalse(improved)
        self.assertFalse(parsed)


class EveryArmGetsTheSamePrompt(unittest.TestCase):
    def test_instruction_asks_for_a_judgement_not_a_completion_report(self):
        text = run_pilot.SELF_VERDICT_INSTRUCTION
        self.assertIn('"improved"', text)
        self.assertIn("confidence", text)
        self.assertIn("false if you are not convinced", text)
        self.assertIn("not a report that you finished editing", text)


class AgentExitStatusIsObserved(unittest.TestCase):
    def test_stdout_does_not_turn_a_failed_process_into_success(self):
        completed = mock.Mock(returncode=7, stdout="plausible summary", stderr="failure")
        with mock.patch.object(run_pilot.subprocess, "run", return_value=completed):
            summary, ok, full_stdout = run_pilot.run_agent("", "test-model")
        self.assertEqual(summary, "plausible summary")
        self.assertEqual(full_stdout, "plausible summary")
        self.assertFalse(ok)

    def test_clean_exit_is_success_even_when_stdout_is_empty(self):
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(run_pilot.subprocess, "run", return_value=completed):
            _, ok, _ = run_pilot.run_agent("", "test-model")
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
