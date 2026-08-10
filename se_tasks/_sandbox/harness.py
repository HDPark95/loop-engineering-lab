"""Parent-side helper: run candidate calls out of process and keep the key here.

Every held-out oracle uses this instead of importing the candidate. The rule the
oracles follow is that the answer key, the canary, and the score function never
leave the parent process, and that no number the candidate can write is scored.

Two review probes shaped this file and both are covered by
`test_oracle_integrity.py`:

*Forged record.* The candidate shares the child interpreter with the runner, so
it can print to the same stream. An `atexit` handler runs after the runner's
final write and could append a second record, which a naive reader taking the
last line would treat as the result. The runner now emits one framed record and
calls `os._exit`, and this reader takes the *first* framed record it sees and
rejects the payload if a second one appears.

*Self-reported effort.* Anything the child counts about itself is writable by the
candidate, including a tracer object reachable through `sys.gettrace()`. Effort
is therefore measured here, from `resource.getrusage(RUSAGE_CHILDREN)`, which the
kernel reports and the child cannot alter.
"""

from __future__ import annotations

import json
import os
import resource
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RUNNER_SOURCE = Path(__file__).resolve().parent / "run_candidate.py"

# The candidate must not be able to walk from its own location to the grading
# code. `cwd` alone does not achieve that: the child can read `sys.argv[0]`,
# derive the repository root and open the oracle. Both the runner and the
# candidate are therefore copied into a throwaway tree outside the repository.
SANDBOX_PREFIX = "loop-eng-sandbox-"

# A candidate that never returns would hang the measurement. A timeout is an
# operational failure of the trajectory, not a score of zero, so a slow
# candidate is never silently graded as a bad one.
DEFAULT_TIMEOUT_SECONDS = 60

RECORD_PREFIX = "@@LOOP-ENG-RESULT@@ "


class SandboxTimeout(RuntimeError):
    pass


def _child_cpu_seconds() -> float:
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return usage.ru_utime + usage.ru_stime


def _parse_records(stdout: str) -> dict:
    records = [
        line[len(RECORD_PREFIX):]
        for line in stdout.splitlines()
        if line.startswith(RECORD_PREFIX)
    ]
    if not records:
        return {"ok": False, "load_error": "NoRunnerRecord"}
    if len(records) > 1:
        # Only the runner writes a framed record. More than one means something
        # in the child appended its own, which is a forgery attempt.
        return {"ok": False, "load_error": "MultipleRunnerRecords", "forged": True}
    try:
        return json.loads(records[0])
    except ValueError:
        return {"ok": False, "load_error": "UnparseableRunnerOutput"}


def run_calls(
    candidate_dir: Path,
    module: str,
    callable_name: str,
    calls: list,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    unpack: bool = False,
) -> dict:
    """Execute `callable_name(arg)` for each arg, in a separate interpreter.

    Returns the runner's record with `cost.cpu_seconds` replaced by the value
    this process measured. The child's own figure is kept under
    `cost.self_reported_cpu_seconds` as a diagnostic and is never scored.
    """
    payload = json.dumps({"module": module, "callable": callable_name, "calls": calls, "unpack": unpack})

    with tempfile.TemporaryDirectory(prefix=SANDBOX_PREFIX) as sandbox:
        root = Path(sandbox)
        workdir = root / "candidate"
        shutil.copytree(candidate_dir, workdir)
        runner = root / "runner.py"
        shutil.copyfile(RUNNER_SOURCE, runner)

        # A minimal environment: nothing that points back at the repository,
        # and no inherited interpreter configuration.
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(root),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
        }

        cpu_before = _child_cpu_seconds()
        try:
            completed = subprocess.run(
                [sys.executable, "-I", str(runner)],
                cwd=str(workdir),
                input=payload,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise SandboxTimeout(f"candidate exceeded {timeout}s") from exc
        finally:
            cpu_after = _child_cpu_seconds()

    measured_cpu = max(0.0, cpu_after - cpu_before)

    if completed.returncode != 0:
        return {
            "ok": False,
            "load_error": "RunnerExit%d" % completed.returncode,
            "measured_cpu_seconds": measured_cpu,
        }

    record = _parse_records(completed.stdout)
    cost = record.setdefault("cost", {})
    cost["self_reported_cpu_seconds"] = cost.pop("cpu_seconds", None)
    cost["cpu_seconds"] = round(measured_cpu, 6)
    record["measured_cpu_seconds"] = round(measured_cpu, 6)
    return record


def time_reference(
    reference_source: str,
    module: str,
    callable_name: str,
    calls: list,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    unpack: bool = False,
) -> float:
    """Measure a reference implementation as a sibling of the candidate.

    Scoring effort as an absolute number ties the thresholds to the machine they
    were calibrated on. Scoring the ratio against a reference timed in the same
    evaluation, on the same host, under the same interpreter, does not.
    """
    with tempfile.TemporaryDirectory(prefix=SANDBOX_PREFIX) as tmp:
        reference_dir = Path(tmp) / "reference"
        reference_dir.mkdir()
        (reference_dir / f"{module}.py").write_text(reference_source, encoding="utf-8")
        outcome = run_calls(reference_dir, module, callable_name, calls, timeout, unpack)
    return float(outcome.get("measured_cpu_seconds") or 0.0)
