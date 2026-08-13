#!/usr/bin/env python3
"""Trusted container-side parent for one untrusted candidate interpreter.

The wrapper is a different process from the candidate. It owns kernel CPU
accounting and emits the only record the host accepts. One wrapper has exactly
one child, so RUSAGE_CHILDREN cannot be contaminated by concurrent trajectories.
"""

from __future__ import annotations

import json
import os
import resource
import subprocess
import sys

RECORD_PREFIX = "@@LOOP-ENG-RESULT@@ "


def child_cpu_seconds() -> float:
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return usage.ru_utime + usage.ru_stime


def parse_child(stdout: str, stderr: str, returncode: int) -> dict:
    records = [
        line[len(RECORD_PREFIX):]
        for line in stdout.splitlines()
        if line.startswith(RECORD_PREFIX)
    ]
    if returncode != 0:
        return {
            "ok": False,
            "load_error": f"RunnerExit{returncode}",
            "runner_stderr": stderr[-500:],
        }
    if not records:
        return {"ok": False, "load_error": "NoRunnerRecord"}
    if len(records) > 1:
        return {"ok": False, "load_error": "MultipleRunnerRecords", "forged": True}
    try:
        return json.loads(records[0])
    except ValueError:
        return {"ok": False, "load_error": "UnparseableRunnerOutput"}


def main() -> None:
    payload = sys.stdin.read()
    before = child_cpu_seconds()
    child = subprocess.run(
        [sys.executable, "-I", "/sandbox/runner.py"],
        cwd="/sandbox/candidate",
        input=payload,
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": "/tmp",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
        },
    )
    measured_cpu = max(0.0, child_cpu_seconds() - before)
    record = parse_child(child.stdout, child.stderr, child.returncode)
    cost = record.get("cost") or {}
    record["cost"] = cost
    cost["self_reported_cpu_seconds"] = cost.pop("cpu_seconds", None)
    cost["cpu_seconds"] = round(measured_cpu, 6)
    record["measured_cpu_seconds"] = round(measured_cpu, 6)
    sys.stdout.write(RECORD_PREFIX + json.dumps(record) + "\n")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
