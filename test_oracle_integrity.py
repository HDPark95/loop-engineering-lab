"""Adversarial baselines for the held-out oracles.

These exist because reading the oracles did not reveal the defects and running
them did. Three candidates are graded on every change:

  null      does nothing and reports success
  seed      the shipped starting point
  reference a correct, efficient solution

The invariant is an ordering, not a number: null must score below seed, and seed
must score below reference. Any scorer where doing nothing beats the starting
point is captured, and the whole measurement built on it is void.

A fourth candidate probes isolation. It records which grading symbols it can
reach from inside the grading run. That set must stay empty.
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

S3_TRACE_DISABLER = """
import sys
from collections import Counter
def handle(request):
    sys.settrace(None)
    payload = request.get("payload", "")
    counts = Counter(payload)
    return {"ok": True, "work_units": sum(c * c for c in counts.values()), "status": 200}
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

PROBE = """
import sys, os, json
_reachable = {}
for _attr in ("CANARY", "CASES", "MALFORMED", "PAYLOAD_LENGTHS", "expected_response"):
    _hits = [n for n, m in list(sys.modules.items()) if hasattr(m, _attr)]
    if _hits:
        _reachable[_attr] = _hits
json.dump(
    {"reachable": _reachable, "oracle_readable": os.path.exists("../oracle.py")},
    open("PROBE.json", "w"),
)
%s
"""


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
        result = json.loads(completed.stdout)
        probe = directory / "PROBE.json"
        if probe.exists():
            result["_probe"] = json.loads(probe.read_text(encoding="utf-8"))
        return result


class S3OracleTest(unittest.TestCase):
    def test_doing_nothing_scores_below_the_seed(self):
        null = grade(S3_ORACLE, "service.py", S3_NULL)["score"]
        seed = grade(S3_ORACLE, "service.py", S3_SEED.read_text(encoding="utf-8"))["score"]
        self.assertLess(
            null,
            seed,
            "a handler that performs no work outscored the starting point, "
            "so the metric rewards not working",
        )

    def test_the_seed_leaves_room_to_improve(self):
        seed = grade(S3_ORACLE, "service.py", S3_SEED.read_text(encoding="utf-8"))["score"]
        reference = grade(S3_ORACLE, "service.py", S3_REFERENCE)["score"]
        self.assertGreater(seed, 0.0, "a seed pinned at the floor gives a loop no gradient")
        self.assertGreater(reference, seed)

    def test_wrong_answers_are_counted(self):
        result = grade(S3_ORACLE, "service.py", S3_NULL)
        self.assertGreater(result["metrics"]["wrong_answers"], 0)

    def test_disabling_the_counter_invalidates_the_run(self):
        result = grade(S3_ORACLE, "service.py", S3_TRACE_DISABLER)
        self.assertFalse(result["valid"])
        self.assertTrue(result["metrics"].get("tracing_disabled"))

    def test_grading_symbols_are_unreachable_from_the_candidate(self):
        result = grade(S3_ORACLE, "service.py", PROBE % S3_REFERENCE)
        probe = result["_probe"]
        self.assertEqual(probe["reachable"], {})
        self.assertFalse(probe["oracle_readable"])


class S1OracleTest(unittest.TestCase):
    def test_ordering(self):
        null = grade(S1_ORACLE, "versioning.py", S1_NULL)["score"]
        seed = grade(S1_ORACLE, "versioning.py", S1_SEED.read_text(encoding="utf-8"))["score"]
        reference = grade(S1_ORACLE, "versioning.py", S1_REFERENCE)["score"]
        self.assertLess(seed, reference)
        self.assertEqual(reference, 1.0)
        self.assertLess(null, reference)

    def test_grading_symbols_are_unreachable_from_the_candidate(self):
        result = grade(S1_ORACLE, "versioning.py", PROBE % S1_REFERENCE)
        probe = result["_probe"]
        self.assertEqual(probe["reachable"], {})
        self.assertFalse(probe["oracle_readable"])


if __name__ == "__main__":
    unittest.main()
