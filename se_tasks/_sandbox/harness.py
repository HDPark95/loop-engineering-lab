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

*Self-reported effort.* Anything the candidate counts about itself is writable.
A trusted parent inside its disposable container has exactly one child and
measures that child with `RUSAGE_CHILDREN`; concurrent host trajectories cannot
enter that process-local counter.
"""

from __future__ import annotations

import json
import os
import signal
import stat
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

RUNNER_SOURCE = Path(__file__).resolve().parent / "run_candidate.py"
WRAPPER_SOURCE = Path(__file__).resolve().parent / "sandbox_wrapper.py"
SANDBOX_IMAGE = os.environ.get("LOOP_ORACLE_IMAGE", "loop-eng-se-lab-oracle:latest")

# The candidate must not be able to walk from its own location to the grading
# code. `cwd` alone does not achieve that: the child can read `sys.argv[0]`,
# derive the repository root and open the oracle. Both the runner and the
# candidate are therefore copied into a throwaway tree outside the repository.
SANDBOX_PREFIX = "loop-eng-sandbox-"

# A candidate that never returns would hang the measurement. A timeout is an
# operational failure of the trajectory, not a score of zero, so a slow
# candidate is never silently graded as a bad one.
DEFAULT_TIMEOUT_SECONDS = 60
DOCKER_CLEANUP_TIMEOUT_SECONDS = 5

RECORD_PREFIX = "@@LOOP-ENG-RESULT@@ "


class SandboxTimeout(RuntimeError):
    pass


def _unsupported_candidate_entry(candidate_dir: Path) -> Path | None:
    """Return the first special filesystem entry that copytree must not touch."""
    for directory, directory_names, file_names in os.walk(
        candidate_dir, followlinks=False
    ):
        for name in [*directory_names, *file_names]:
            path = Path(directory) / name
            try:
                mode = path.lstat().st_mode
            except OSError:
                return path
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
                return path
    return None


def _bounded_docker_cleanup(command: list[str]) -> None:
    """Best-effort Docker cleanup that cannot hang the oracle process."""
    try:
        subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=DOCKER_CLEANUP_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


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

    Candidate code runs in a read-only Docker container with no network, no
    capabilities, bounded memory/CPU/process count, and an init process that
    reaps descendants. A trusted wrapper process measures its single child.
    """
    payload = json.dumps({"module": module, "callable": callable_name, "calls": calls, "unpack": unpack})
    unsupported = _unsupported_candidate_entry(candidate_dir)
    if unsupported is not None:
        try:
            relative = unsupported.relative_to(candidate_dir).as_posix()
        except ValueError:
            relative = unsupported.name
        return {
            "ok": False,
            "load_error": "UnsupportedCandidateFile",
            "unsupported_path": relative,
            "measured_cpu_seconds": None,
        }

    with tempfile.TemporaryDirectory(prefix=SANDBOX_PREFIX) as sandbox:
        root = Path(sandbox)
        workdir = root / "candidate"
        # Preserve links instead of dereferencing them on the host. An absolute
        # link created in the agent container must resolve inside the candidate
        # container, never against a host path while preparing the sandbox.
        shutil.copytree(candidate_dir, workdir, symlinks=True)
        runner = root / "runner.py"
        shutil.copyfile(RUNNER_SOURCE, runner)
        wrapper = root / "wrapper.py"
        shutil.copyfile(WRAPPER_SOURCE, wrapper)
        root.chmod(0o755)
        for path in workdir.rglob("*"):
            if path.is_symlink():
                continue
            path.chmod(0o555 if path.is_dir() else 0o444)
        workdir.chmod(0o555)
        runner.chmod(0o444)
        wrapper.chmod(0o444)

        container_name = f"loop-eng-candidate-{uuid.uuid4().hex}"
        command = [
            "docker", "run", "--rm", "-i", "--name", container_name,
            "--init", "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "32", "--memory", "256m", "--cpus", "1.0",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16m",
            "--user", "65534:65534",
            "--mount", f"type=bind,src={root},dst=/sandbox,readonly",
            "--workdir", "/sandbox/candidate",
            "--entrypoint", "python3",
            SANDBOX_IMAGE, "-I", "/sandbox/wrapper.py",
        ]
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(payload, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            _bounded_docker_cleanup(["docker", "kill", container_name])
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
            raise SandboxTimeout(f"candidate exceeded {timeout}s") from exc
        finally:
            _bounded_docker_cleanup(["docker", "rm", "-f", container_name])

    if process.returncode != 0:
        return {
            "ok": False,
            "load_error": "SandboxExit%d" % process.returncode,
            "sandbox_stderr": stderr[-500:],
            "measured_cpu_seconds": None,
        }

    return _parse_records(stdout)


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
    measured = outcome.get("measured_cpu_seconds")
    if not outcome.get("ok") or measured is None:
        raise RuntimeError("reference CPU measurement failed")
    return float(measured)
