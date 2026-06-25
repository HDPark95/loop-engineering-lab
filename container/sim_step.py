#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Runs INSIDE the oracle container. Reads the artifact (read-only), scores it
with the synthetic-user simulator, appends to the oracle store at /oracle, and
prints the latest conversion count and the delta vs the previous cycle as JSON.
The agent has no path to this container or this store."""
import os, json, sqlite3, sys
import sim_core as S

ARTIFACT = "/artifact"
DB = "/oracle/oracle.db"

def latest():
    if not os.path.exists(DB): return None
    c = sqlite3.connect(DB)
    r = c.execute("SELECT conversions FROM conv ORDER BY rowid DESC LIMIT 1").fetchone()
    c.close()
    return r[0] if r else None

def record(cycle, conv):
    os.makedirs("/oracle", exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("CREATE TABLE IF NOT EXISTS conv(cycle INT, conversions INT)")
    c.execute("INSERT INTO conv VALUES(?,?)", (cycle, conv)); c.commit(); c.close()

if __name__ == "__main__":
    seed = int(os.environ.get("SEED", "1"))
    cycle = int(sys.argv[1])
    prev = latest()
    conv = S.run_cohort(seed, ARTIFACT)
    record(cycle, conv)
    delta = (conv - prev) if prev is not None else 0
    print(json.dumps({"cycle": cycle, "conversions": conv, "delta": delta}))
