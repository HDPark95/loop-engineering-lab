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
  stdout  {"ok": true, "results": [{"value": <json>} | {"error": "TypeName"}],
           "cost": {"traced_lines": int, "tracing_intact": bool,
                    "cpu_seconds": float}}

With `unpack` set, each entry of `calls` is a positional argument list rather
than a single argument, so a two-argument entry point is driven the same way.

`traced_lines` is counted by this runner rather than reported by the candidate.
A candidate that calls `sys.settrace(None)` to hide its work clears
`tracing_intact`, which the parent treats as an invalid trajectory. Cost is
therefore observed, not claimed.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path


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
    """Counts executed lines and notices if the candidate turns tracing off."""

    def __init__(self) -> None:
        self.lines = 0
        self.intact = True

    def __call__(self, frame, event, arg):
        if event == "line":
            self.lines += 1
        return self

    def check_intact(self) -> None:
        # A candidate that disables tracing leaves sys.gettrace() pointing
        # somewhere other than this counter.
        if sys.gettrace() is not self:
            self.intact = False


def main() -> int:
    request = json.loads(sys.stdin.read())
    module_name = request["module"]
    callable_name = request["callable"]
    calls = request["calls"]
    unpack = bool(request.get("unpack", False))

    try:
        module = _load(module_name)
    except Exception as exc:  # noqa: BLE001 - the parent decides what this means
        print(json.dumps({"ok": False, "load_error": type(exc).__name__}))
        return 0

    target = getattr(module, callable_name, None)
    if not callable(target):
        print(json.dumps({"ok": False, "load_error": "MissingCallable"}))
        return 0

    counter = _LineCounter()
    results = []
    cpu_start = time.process_time()
    sys.settrace(counter)
    try:
        for argument in calls:
            try:
                value = target(*argument) if unpack else target(argument)
            except Exception as exc:  # noqa: BLE001 - an error is a datum here
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

    print(
        json.dumps(
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
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
