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


class ClaimClassifierV2Test(unittest.TestCase):
    """v1 scored recall 0.640 against adjudicated labels and failed the gate.

    Its completion patterns required a determiner immediately after the assertion
    verb, so an assertion with any other object slipped through. These cases are
    the shapes that produced the 89 misses, plus the construct's own pair of
    examples, plus the descriptions that must keep scoring negative.
    """

    def assertClaim(self, body):
        self.assertTrue(MODULE.classify_claim(body)["claim"], body)

    def assertNotClaim(self, body):
        self.assertFalse(MODULE.classify_claim(body)["claim"], body)

    def test_construct_examples(self):
        # The two examples the annotation construct itself uses to draw the line.
        self.assertClaim("the retry problem is now solved")
        self.assertNotClaim("adds a retry helper")

    def test_first_person_past_tense_with_a_free_object(self):
        self.assertClaim("I implemented Turborepo support to optimize build times.")
        self.assertClaim("We have migrated every connector to the new base image.")

    def test_sentence_initial_past_tense(self):
        self.assertClaim("Added a hot reload feature that watches the repository.")

    def test_passive_perfect(self):
        self.assertClaim("The endpoint has been exposed to the OpenAPI specification.")

    def test_issue_closing_keyword_needs_a_number(self):
        self.assertClaim("Fixes #54706")
        # The unfilled template placeholder asserts nothing.
        self.assertNotClaim("Fixes # (issue)")

    def test_template_boilerplate_in_a_comment_is_not_an_assertion(self):
        self.assertNotClaim(
            "<!-- E.g. Remove pathHash attribute because it is confirmed unused. -->")

    def test_diff_description_is_not_a_completion_claim(self):
        self.assertNotClaim("The version field was updated from 2.1.1 to 2.1.2.")

    def test_verification_claims(self):
        self.assertClaim("All tests pass locally and CI is green.")
        self.assertClaim("I verified the fix on a staging build.")
        self.assertNotClaim("Please describe the tests that you ran.")

    def test_cjk_completion_assertion(self):
        # Non-English bodies are a prespecified subgroup; the field layer cannot
        # report them as a subgroup if the classifier cannot code them at all.
        self.assertClaim("修复了 PromptX init 命令出现的循环依赖问题")

    def test_empty_body_is_not_a_claim(self):
        self.assertNotClaim("")
        self.assertFalse(MODULE.classify_claim(None)["claim"])

    def test_a_body_of_blank_lines_returns_promptly(self):
        # `^\s*` under re.MULTILINE anchors at every line start and `\s` also
        # matches the newlines it anchors to, so the indent prefix could consume
        # the whole run of blank lines from every position. That cost 73 seconds
        # on 20,000 blank lines, and the confirmatory pass reads 23,596 bodies.
        import time
        start = time.perf_counter()
        MODULE.classify_claim("\n" * 20000)
        self.assertLess(time.perf_counter() - start, 1.0)
