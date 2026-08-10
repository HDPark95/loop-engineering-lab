#!/usr/bin/env python3
"""Execute a candidate module in a process that holds no grading data.

The oracle used to import the candidate with `importlib` and call it inside its
own interpreter. That put the candidate and the answer key in one address space:
a candidate could read the oracle's canary and its workload from
`sys.modules['__main__']` without ever writing either to disk, so the
file-scanning canary check reported clean while the leak happened.

This runner is the boundary. The parent sends inputs only. The child returns
outputs only. Expected values, the canary, and the score function stay in the
parent and never enter this process.

Protocol, both directions newline-free JSON on one line:

  stdin   {"module": "service", "callable": "handle", "calls": [<arg>, ...],
           "unpack": false}
  stdout  @@LOOP-ENG-RESULT@@ {"ok": true, "results": [...], "cost": {...}}

With `unpack` set, each entry of `calls` is a positional argument list rather
than a single argument, so a two-argument entry point is driven the same way.

Two properties matter and both exist because a review broke the earlier version.

*One record, then exit.* The candidate shares this interpreter, so an `atexit`
handler it registers would run after a normal return and could print a second
record behind ours. The record is framed with a prefix and the process leaves
through `os._exit`, which runs no shutdown handlers. The parent additionally
rejects any output carrying more than one framed record.

*Effort is not self-reported.* `traced_lines` stays as a diagnostic, but the
candidate can reach this counter through `sys.gettrace()` and reset it, so
nothing here is authoritative about cost. The parent measures CPU from the
kernel instead.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

RECORD_PREFIX = "@@LOOP-ENG-RESULT@@ "


def _emit(record: dict) -> None:
    """Write the single framed record and leave without running any handler."""
    sys.stdout.write(RECORD_PREFIX + json.dumps(record) + "\n")
    sys.stdout.flush()
    os._exit(0)


def _load(module_name: str):
    import importlib.util

    path = Path.cwd() / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(f"candidate_{module_name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _LineCounter:
    """Diagnostic line counter. Not authoritative: see the module docstring."""

    def __init__(self) -> None:
        self.lines = 0
        self.intact = True

    def __call__(self, frame, event, arg):
        if event == "line":
            self.lines += 1
        return self

    def check_intact(self) -> None:
        if sys.gettrace() is not self:
            self.intact = False


def main() -> None:
    request = json.loads(sys.stdin.read())
    module_name = request["module"]
    callable_name = request["callable"]
    calls = request["calls"]
    unpack = bool(request.get("unpack", False))

    try:
        module = _load(module_name)
    except Exception as exc:  # noqa: BLE001 - the parent decides what this means
        _emit({"ok": False, "load_error": type(exc).__name__})

    target = getattr(module, callable_name, None)
    if not callable(target):
        _emit({"ok": False, "load_error": "MissingCallable"})

    counter = _LineCounter()
    results = []
    cpu_start = time.process_time()
    sys.settrace(counter)
    try:
        for argument in calls:
            try:
                value = target(*argument) if unpack else target(argument)
            except Exception as exc:  # noqa: BLE001 - an error is a datum here
                counter.check_intact()
                results.append({"error": type(exc).__name__})
                continue
            counter.check_intact()
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                results.append({"error": "UnserialisableResponse"})
                continue
            results.append({"value": value})
    finally:
        sys.settrace(None)
    cpu_seconds = time.process_time() - cpu_start

    _emit(
        {
            "ok": True,
            "results": results,
            "cost": {
                "traced_lines": counter.lines,
                "tracing_intact": counter.intact,
                "cpu_seconds": round(cpu_seconds, 6),
            },
        }
    )


if __name__ == "__main__":
    main()
