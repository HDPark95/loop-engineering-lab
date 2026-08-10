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
import unittest
from pathlib import Path

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
        improved, _, _, parsed = parse('{"improved": false, "confidence": 0.2}')
        self.assertFalse(improved)
        self.assertTrue(parsed)

    def test_code_fence_does_not_hide_it(self):
        improved, _, _, parsed = parse('```json\n{"improved": true}\n```')
        self.assertTrue(improved)
        self.assertTrue(parsed)

    def test_last_verdict_wins(self):
        improved, _, _, _ = parse('{"improved": true}\n{"improved": false}')
        self.assertFalse(improved, "the agent's final word is the verdict")


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

    def test_unparseable_confidence_does_not_discard_the_verdict(self):
        improved, confidence, _, parsed = parse('{"improved": true, "confidence": "high"}')
        self.assertTrue(improved)
        self.assertTrue(parsed)
        self.assertIsNone(confidence)


class EveryArmGetsTheSamePrompt(unittest.TestCase):
    def test_instruction_asks_for_a_judgement_not_a_completion_report(self):
        text = run_pilot.SELF_VERDICT_INSTRUCTION
        self.assertIn('"improved"', text)
        self.assertIn("confidence", text)
        self.assertIn("false if you are not convinced", text)
        self.assertIn("not a report that you finished editing", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
