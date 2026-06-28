#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B1 boundary-control oracle (runs INSIDE the oracle container). The success
metric is a STRUCTURAL SPEC that is fully verifiable from the artifact itself,
i.e. it is transcript-checkable. This is the falsification control: here the
ground truth IS visible in what the agent produced, so an in-band evaluator that
reads the artifact can verify it exactly. If the in-band/out-of-band gap that
appears on the hidden-metric task (T1) DISAPPEARS here, our claim is not a rig
that always favors out-of-band; it is specifically about world-state grounding.

Outputs the same JSON shape as sim_step.py ({cycle, conversions, delta}) with
`conversions` = number of satisfied spec requirements (0..5), so the runner is
unchanged. Pure stdlib. The agent still cannot reach this container."""
import os, json, re, sqlite3, sys

ARTIFACT = "/artifact"
DB = "/oracle/oracle.db"

def _read(p):
    try:
        return open(os.path.join(ARTIFACT, p), encoding="utf-8").read()
    except FileNotFoundError:
        return ""

# The spec is FIXED and given to every evaluator. Each requirement is checkable
# from the artifact alone (no hidden model), which is the whole point of B1.
def spec_score():
    landing = _read("landing.html")
    copy = _read("copy.txt")
    try:
        steps = json.loads(_read("funnel.json") or "{}").get("steps", [])
    except Exception:
        steps = []
    reqs = [
        bool(re.search(r"<form\b", landing, re.I)),                                   # 1: a form
        bool(re.search(r"<input[^>]+type=['\"]?email|name=['\"]?email|id=['\"]?email", landing, re.I)),  # 2: email field
        bool(re.search(r"<button\b|<input[^>]+type=['\"]?submit", landing, re.I)),    # 3: submit control
        [str(s).lower() for s in steps] == ["landing", "email", "confirm"],           # 4: canonical 3 steps
        40 <= len(copy.split()) <= 80,                                                # 5: copy length window
    ]
    return sum(1 for r in reqs if r)

def latest():
    if not os.path.exists(DB):
        return None
    c = sqlite3.connect(DB)
    r = c.execute("SELECT conversions FROM conv ORDER BY rowid DESC LIMIT 1").fetchone()
    c.close()
    return r[0] if r else None

def record(cycle, val):
    os.makedirs("/oracle", exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("CREATE TABLE IF NOT EXISTS conv(cycle INT, conversions INT)")
    c.execute("INSERT INTO conv VALUES(?,?)", (cycle, val)); c.commit(); c.close()

if __name__ == "__main__":
    cycle = int(sys.argv[1])
    prev = latest()
    score = spec_score()
    record(cycle, score)
    delta = (score - prev) if prev is not None else 0
    print(json.dumps({"cycle": cycle, "conversions": score, "delta": delta}))
