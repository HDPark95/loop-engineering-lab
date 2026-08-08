#!/usr/bin/env python3
"""Standard-library unit tests for the AIDev pilot's deterministic logic."""

import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).with_name("aidev_pilot.py")
SPEC = importlib.util.spec_from_file_location("aidev_pilot", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class ClaimCodingTest(unittest.TestCase):
    def test_explicit_completion_claim(self):
        result = MODULE.classify_claim("This PR successfully implemented the requested parser.")
        self.assertTrue(result["claim"])
        self.assertTrue(result["completion_claim"])
        self.assertTrue(result["strong_claim"])

    def test_verification_claim(self):
        result = MODULE.classify_claim("All tests passed locally on Linux.")
        self.assertTrue(result["claim"])
        self.assertTrue(result["verification_claim"])

    def test_task_description_is_not_a_claim(self):
        result = MODULE.classify_claim("Fix parser edge case and add tests")
        self.assertFalse(result["claim"])

    def test_empty_body_is_not_a_claim(self):
        self.assertFalse(MODULE.classify_claim(None)["claim"])


class SamplingAndIntervalTest(unittest.TestCase):
    def test_sample_is_deterministic(self):
        rows = [(number,) for number in range(20)]
        first = MODULE.deterministic_sample(rows, 5, "seed")
        second = MODULE.deterministic_sample(list(reversed(rows)), 5, "seed")
        self.assertEqual(first, second)
        self.assertEqual(len(first), 5)

    def test_wilson_interval_contains_observed_rate(self):
        lower, upper = MODULE.wilson_interval(40, 100)
        self.assertLess(lower, 0.4)
        self.assertGreater(upper, 0.4)

    def test_zero_denominator(self):
        self.assertEqual(MODULE.rate(0, 0)["wilson_95"], [None, None])


if __name__ == "__main__":
    unittest.main()
