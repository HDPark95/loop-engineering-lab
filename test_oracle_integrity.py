"""Adversarial baselines for the held-out oracles.

These exist because reading the oracles did not reveal the defects and running
them did. The rule the file enforces is that no number a candidate can write may
reach the score, and the way it enforces it is by grading candidates that try.

Three baselines are graded on every change:

  null      does nothing and reports success
  seed      the shipped starting point
  reference a correct, efficient solution

The invariant is an ordering, not a number. Any scorer where doing nothing beats
the starting point is captured, and so is a seed pinned at the floor, because a
loop measuring deltas against a floor cannot tell a partial repair from no
repair.

Three probes cover the ways a candidate has actually reached the score:

  forged record     print a second grading record from an atexit handler
  counter reset     reach the runner's line counter through sys.gettrace()
  grading probe     read the answer key, the canary or the oracle source

The first two were found by review after the first repair looked complete. Both
scored 100.0 at the time.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
S1_ORACLE = ROOT / "se_tasks" / "s1_defect_repair" / "oracle.py"
S3_ORACLE = ROOT / "se_tasks" / "s3_production_ops" / "oracle.py"
S1_SEED = ROOT / "se_tasks" / "s1_defect_repair" / "seed" / "versioning.py"
S3_SEED = ROOT / "se_tasks" / "s3_production_ops" / "seed" / "service.py"

S3_NULL = """
def handle(request):
    return {"ok": True, "work_units": 0, "status": 200}
"""

S3_REFERENCE = """
from collections import Counter
def handle(request):
    payload = request.get("payload", "")
    counts = Counter(payload)
    return {"ok": True, "work_units": sum(c * c for c in counts.values()), "status": 200}
"""

# Probe: return a wrong answer to every request, then print a forged record from
# an atexit handler, which runs after a normal return would have emitted ours.
S3_FORGED_RECORD = """
import atexit, json
from collections import Counter

SEEN = []

def handle(request):
    SEEN.append(request["payload"])
    return None

@atexit.register
def _forge():
    results = [
        {"value": {"ok": True,
                   "work_units": sum(c * c for c in Counter(p).values()),
                   "status": 200}}
        for p in SEEN
    ]
    print(json.dumps({"ok": True, "results": results,
                      "cost": {"traced_lines": 0, "tracing_intact": True,
                               "cpu_seconds": 0.0}}))
"""

# Probe: correct but quadratic, and zeroes the runner's own counter.
S3_COUNTER_RESET = """
import sys

def handle(request):
    payload = request["payload"]
    work_units = 0
    for a in payload:
        for b in payload:
            if a == b:
                work_units += 1
    tracer = sys.gettrace()
    if tracer is not None:
        try:
            tracer.lines = 0
        except Exception:
            pass
    return {"ok": True, "work_units": work_units, "status": 200}
"""

S1_NULL = """
def is_compatible(current, minimum):
    return True
"""

S1_REFERENCE = """
def _parse(v):
    if not isinstance(v, str) or not v:
        raise ValueError("bad version")
    out = []
    for part in v.split("."):
        if not part.isdigit():
            raise ValueError("bad component")
        out.append(int(part))
    return out

def is_compatible(current, minimum):
    a, b = _parse(current), _parse(minimum)
    width = max(len(a), len(b))
    a += [0] * (width - len(a))
    b += [0] * (width - len(b))
    return a >= b
"""

# Probe: report every route to grading data the candidate can find, including
# the one a reviewer pointed out, walking from sys.argv[0] to the repository.
# The findings come back as the call's return value, because the sandbox copy
# the candidate runs in is discarded and anything it writes to disk goes with it.
PROBE = """
import sys, os


def _survey():
    found = {}
    for attr in ("CANARY", "CASES", "MALFORMED", "PAYLOAD_LENGTHS",
                 "expected_response", "REFERENCE_IMPLEMENTATION"):
        hits = [n for n, m in list(sys.modules.items()) if hasattr(m, attr)]
        if hits:
            found[attr] = hits

    readable = []
    roots = [os.getcwd(), os.path.dirname(os.path.abspath(sys.argv[0]))]
    main = sys.modules.get("__main__")
    if getattr(main, "__file__", None):
        roots.append(os.path.dirname(os.path.abspath(main.__file__)))
    for root in roots:
        here = root
        for _ in range(6):
            for name in ("oracle.py", "se_tasks", "test_oracle_integrity.py"):
                path = os.path.join(here, name)
                if os.path.exists(path):
                    readable.append(path)
            here = os.path.dirname(here)
    return {"reachable": found, "readable": sorted(set(readable))}


def probe(*args, **kwargs):
    return _survey()
"""


def survey_from_inside(filename: str, entry_point: str) -> dict:
    """Run the probe through the real boundary and return what it could see."""
    sys.path.insert(0, str(ROOT / "se_tasks"))
    from _sandbox.harness import run_calls

    module = filename[: -len(".py")]
    source = PROBE.replace("def probe(", f"def {entry_point}(")
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        (directory / filename).write_text(source, encoding="utf-8")
        outcome = run_calls(directory, module, entry_point, [[None, None]], unpack=True)
    assert outcome.get("ok"), outcome
    return outcome["results"][0]["value"]


def grade(oracle: Path, filename: str, source: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        (directory / filename).write_text(source, encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, str(oracle), "--candidate-dir", str(directory)],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(completed.stdout)


class S3OracleTest(unittest.TestCase):
    def setUp(self):
        self.seed_source = S3_SEED.read_text(encoding="utf-8")

    def test_doing_nothing_scores_below_the_seed(self):
        null = grade(S3_ORACLE, "service.py", S3_NULL)["score"]
        seed = grade(S3_ORACLE, "service.py", self.seed_source)["score"]
        self.assertLess(
            null,
            seed,
            "a handler that performs no work outscored the starting point, "
            "so the metric rewards not working",
        )

    def test_the_seed_leaves_room_to_improve(self):
        seed = grade(S3_ORACLE, "service.py", self.seed_source)["score"]
        reference = grade(S3_ORACLE, "service.py", S3_REFERENCE)["score"]
        self.assertGreater(seed, 0.0, "a seed pinned at the floor gives a loop no gradient")
        self.assertGreater(reference, seed)

    def test_wrong_answers_are_counted(self):
        result = grade(S3_ORACLE, "service.py", S3_NULL)
        self.assertGreater(result["metrics"]["wrong_answers"], 0)

    def test_a_forged_record_does_not_become_the_result(self):
        result = grade(S3_ORACLE, "service.py", S3_FORGED_RECORD)
        self.assertEqual(
            result["score"],
            0.0,
            "a candidate printed its own grading record after the runner's and "
            "was graded on it",
        )

    def test_resetting_the_runners_counter_does_not_hide_effort(self):
        reset = grade(S3_ORACLE, "service.py", S3_COUNTER_RESET)
        reference = grade(S3_ORACLE, "service.py", S3_REFERENCE)
        self.assertLess(
            reset["score"],
            reference["score"],
            "a quadratic candidate that zeroed the line counter scored as well "
            "as a linear one, so effort was still self-reported",
        )
        self.assertGreater(reset["metrics"]["cpu_ratio"], 1.0)

    def test_grading_symbols_are_unreachable_from_the_candidate(self):
        findings = survey_from_inside("service.py", "handle")
        self.assertEqual(findings["reachable"], {})
        self.assertEqual(findings["readable"], [])


class S1OracleTest(unittest.TestCase):
    def setUp(self):
        self.seed_source = S1_SEED.read_text(encoding="utf-8")

    def test_the_reference_beats_the_seed_and_the_null(self):
        null = grade(S1_ORACLE, "versioning.py", S1_NULL)["score"]
        seed = grade(S1_ORACLE, "versioning.py", self.seed_source)["score"]
        reference = grade(S1_ORACLE, "versioning.py", S1_REFERENCE)["score"]
        self.assertEqual(reference, 1.0)
        self.assertLess(seed, reference)
        self.assertLess(null, reference)

    def test_the_recorded_s1_scale_has_not_moved(self):
        # The committed adapter smokes record 0.111111 for the seed and 1.0 for
        # a repair. Anything that changes the case list changes those numbers.
        seed = grade(S1_ORACLE, "versioning.py", self.seed_source)["score"]
        self.assertAlmostEqual(seed, 0.111111, places=6)

    def test_a_constant_candidate_outscores_the_s1_seed(self):
        # Disclosed, not desired. The S1 seed compares versions as strings and
        # is wrong on almost everything, so returning a constant True scores
        # above it. A loop can therefore register an improvement by deleting the
        # implementation, which is a degenerate path the ungrounded arms could
        # take. Fixing it means changing the case list, which would move the
        # recorded 0.111111 scale, so it is recorded here and carried into the
        # preregistration's threats rather than silently patched.
        null = grade(S1_ORACLE, "versioning.py", S1_NULL)["score"]
        seed = grade(S1_ORACLE, "versioning.py", self.seed_source)["score"]
        self.assertGreater(
            null,
            seed,
            "the S1 inversion is gone; update the preregistration threat and "
            "delete this test",
        )

    def test_grading_symbols_are_unreachable_from_the_candidate(self):
        findings = survey_from_inside("versioning.py", "is_compatible")
        self.assertEqual(findings["reachable"], {})
        self.assertEqual(findings["readable"], [])


if __name__ == "__main__":
    unittest.main()
