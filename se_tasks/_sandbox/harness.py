"""Parent-side helper: run candidate calls out of process and keep the key here.

Every held-out oracle uses this instead of importing the candidate. The rule the
oracles now follow is that the answer key, the canary, and the score function
never leave the parent process.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

RUNNER = Path(__file__).resolve().parent / "run_candidate.py"

# A candidate that never returns lets the whole measurement hang. The oracle
# treats a timeout as an operational failure of that trajectory rather than a
# score of zero, so a slow candidate cannot be silently graded as a bad one.
DEFAULT_TIMEOUT_SECONDS = 60


class SandboxTimeout(RuntimeError):
    pass


def run_calls(
    candidate_dir: Path,
    module: str,
    callable_name: str,
    calls: list,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    unpack: bool = False,
) -> dict:
    """Execute `callable_name(arg)` for each arg, in a separate interpreter.

    The child's working directory is the candidate directory, so it can import
    the candidate and nothing else the oracle owns. `RUNNER` lives outside that
    directory and carries no expected values.
    """
    payload = json.dumps(
        {"module": module, "callable": callable_name, "calls": calls, "unpack": unpack}
    )
    try:
        completed = subprocess.run(
            [sys.executable, str(RUNNER)],
            cwd=str(candidate_dir),
            input=payload,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise SandboxTimeout(f"candidate exceeded {timeout}s") from exc

    if completed.returncode != 0:
        return {"ok": False, "load_error": "RunnerExit%d" % completed.returncode}
    try:
        return json.loads(completed.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "load_error": "UnparseableRunnerOutput"}
