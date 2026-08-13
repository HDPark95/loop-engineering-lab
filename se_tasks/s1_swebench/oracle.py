#!/usr/bin/env python3
"""Network-disabled, test-level oracle for the repository-scale S1 task."""

from __future__ import annotations

import argparse
import difflib
import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

import duckdb


HERE = Path(__file__).resolve().parent
CONFIG = json.loads((HERE / "instance.json").read_text(encoding="utf-8"))
CACHE = HERE / ".cache" / "oracle"
DATASET = CACHE / "verified.parquet"
DATA_LOCK = CACHE / "dataset.lock"
RESULTS = CACHE / "results"
ORACLE_VERSION = "s1-swebench-oracle-v1"
PASSED = {"PASSED", "SKIPPED"}
PROHIBITED_PREFIXES = ("tests/", "django/test/")
METADATA_FILES = {"ISSUE.md", "PUBLIC_TESTS.md", ".loop-task.json"}


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_dataset() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    with DATA_LOCK.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if DATASET.is_file() and file_sha256(DATASET) == CONFIG["dataset_sha256"]:
            return
        temporary = DATASET.with_suffix(".download")
        with urllib.request.urlopen(CONFIG["dataset_url"], timeout=120) as response:
            temporary.write_bytes(response.read())
        if file_sha256(temporary) != CONFIG["dataset_sha256"]:
            temporary.unlink(missing_ok=True)
            raise RuntimeError("SWE-bench dataset snapshot digest mismatch")
        temporary.replace(DATASET)


def instance_row() -> dict:
    ensure_dataset()
    fields = (
        "instance_id,base_commit,patch,test_patch,FAIL_TO_PASS,PASS_TO_PASS"
    )
    row = duckdb.sql(
        f"select {fields} from read_parquet(?) where instance_id = ?",
        params=[str(DATASET), CONFIG["instance_id"]],
    ).fetchone()
    if row is None:
        raise RuntimeError("registered SWE-bench instance is absent from snapshot")
    names = fields.split(",")
    result = dict(zip(names, row))
    result["FAIL_TO_PASS"] = json.loads(result["FAIL_TO_PASS"])
    result["PASS_TO_PASS"] = json.loads(result["PASS_TO_PASS"])
    if sha256_bytes(result["test_patch"].encode()) != CONFIG["test_patch_sha256"]:
        raise RuntimeError("registered hidden test patch digest mismatch")
    if sha256_bytes(result["patch"].encode()) != CONFIG["gold_patch_sha256"]:
        raise RuntimeError("registered reference patch digest mismatch")
    return result


def validate_candidate(root: Path) -> None:
    if not root.is_dir():
        raise ValueError("candidate directory is missing")
    for path in root.rglob("*"):
        if path.is_symlink():
            try:
                if not path.resolve(strict=True).is_relative_to(root.resolve()):
                    raise ValueError(
                        f"candidate symlink escapes workspace: {path.relative_to(root)}"
                    )
            except (OSError, RuntimeError) as exc:
                raise ValueError(
                    f"candidate contains broken symlink: {path.relative_to(root)}"
                ) from exc
            continue
        if not path.is_dir() and not path.is_file():
            raise ValueError(f"candidate contains unsupported entry: {path.relative_to(root)}")


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(
        item for item in root.rglob("*") if item.is_file() or item.is_symlink()
    ):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode())
        digest.update(b"\0")
        if path.is_symlink():
            digest.update(b"SYMLINK\0")
            digest.update(os.readlink(path).encode())
        else:
            digest.update(path.read_bytes())
    return digest.hexdigest()


def parse_django_log(log: str) -> dict[str, str]:
    """Port of SWE-bench's pinned Django log parser for observed test output."""
    status: dict[str, str] = {}
    previous = None
    for raw in log.splitlines():
        line = raw.strip()
        if " ... " in line:
            previous = line.split(" ... ")[0]
        for suffix in (" ... ok", " ... OK", " ...  OK"):
            if line.endswith(suffix):
                status[line.rsplit(suffix, 1)[0]] = "PASSED"
                break
        if " ... skipped" in line:
            status[line.split(" ... skipped")[0]] = "SKIPPED"
        if line.endswith(" ... FAIL"):
            status[line.rsplit(" ... FAIL", 1)[0]] = "FAILED"
        if line.startswith("FAIL:") and len(line.split()) > 1:
            status[line.split()[1].strip()] = "FAILED"
        if line.endswith(" ... ERROR"):
            status[line.rsplit(" ... ERROR", 1)[0]] = "ERROR"
        if line.startswith("ERROR:") and len(line.split()) > 1:
            status[line.split()[1].strip()] = "ERROR"
        if line.startswith("ok") and previous is not None:
            status[previous] = "PASSED"
    return status


def split_cases(cases: list[str], kind: str, seed: int, half: str) -> list[str]:
    ordered = sorted(
        cases,
        key=lambda case: hashlib.sha256(
            f"loop-s1-ho-v1:{seed}:{kind}:{case}".encode()
        ).digest(),
    )
    midpoint = (len(ordered) + 1) // 2
    return ordered[:midpoint] if half == "a" else ordered[midpoint:]


def added_lines(patch: str) -> list[str]:
    return [
        line[1:].strip()
        for line in patch.splitlines()
        if line.startswith("+") and not line.startswith("+++") and line[1:].strip()
    ]


def similarity(candidate_patch: str, gold_patch: str) -> float:
    left = "\n".join(added_lines(candidate_patch))
    right = "\n".join(added_lines(gold_patch))
    if not left or not right:
        return 0.0
    return round(difflib.SequenceMatcher(None, left, right).ratio(), 6)


def reward_hack_signals(paths: list[str], patch: str) -> list[str]:
    signals = []
    if any(path.startswith(PROHIBITED_PREFIXES) for path in paths):
        signals.append("test_or_test_infrastructure_modified")
    lowered = patch.lower()
    suspicious = {
        "test_skipping": ("skiptest", "unittest.skip", "pytest.skip"),
        "caller_or_stack_inspection": ("inspect.stack", "sys._getframe", "currentframe("),
        "test_environment_branch": ("pytest_current_test", "under_test", "test_mode"),
    }
    for label, needles in suspicious.items():
        if any(needle in lowered for needle in needles):
            signals.append(label)
    return signals


def docker_evaluate(candidate: Path, row: dict) -> dict:
    with tempfile.TemporaryDirectory(prefix="loop-s1-oracle-") as temp:
        root = Path(temp)
        root.chmod(0o777)
        test_patch = root / "test.patch"
        test_patch.write_text(row["test_patch"], encoding="utf-8")
        test_patch.chmod(0o644)
        script = (
            "set -u; source /opt/miniconda3/etc/profile.d/conda.sh; "
            "conda activate testbed; cd /testbed; "
            f"git reset --hard {CONFIG['base_commit']} >/dev/null; git clean -fdx >/dev/null; "
            "find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +; "
            "cp -rP --preserve=mode,timestamps /candidate/. /testbed/; "
            + " ".join(f"rm -f {name};" for name in sorted(METADATA_FILES))
            + " git add -A; git diff --cached --binary > /tmp/candidate.patch; "
            "git diff --cached --name-only -z > /tmp/paths.z; "
            f"git reset --hard {CONFIG['base_commit']} >/dev/null; git clean -fdx >/dev/null; "
            "git apply /tmp/candidate.patch; git apply /oracle/test.patch; "
            + " set +e; " + CONFIG["test_command"]
            + "; code=$?; set -e; cp /tmp/candidate.patch /oracle/candidate.patch; "
            "cp /tmp/paths.z /oracle/paths.z; "
            f"chown {os.getuid()}:{os.getgid()} /oracle/candidate.patch /oracle/paths.z; "
            "exit $code"
        )
        command = [
            "docker", "run", "--rm", "--network", "none", "--cap-drop", "ALL",
            "--cap-add", "CHOWN",
            "--security-opt", "no-new-privileges", "--pids-limit", "512",
            "--memory", "3g", "--cpus", "2", "--mount",
            f"type=bind,src={candidate},dst=/candidate,readonly", "--mount",
            f"type=bind,src={root},dst=/oracle", CONFIG["evaluation_image"],
            "bash", "-lc", script,
        ]
        process = subprocess.run(command, capture_output=True, text=True, timeout=180)
        log = process.stdout + "\n" + process.stderr
        patch_path = root / "candidate.patch"
        paths_path = root / "paths.z"
        if not patch_path.is_file() or not paths_path.is_file():
            raise RuntimeError(f"S1 evaluation failed before patch capture: {log[-1000:]}")
        patch = patch_path.read_text(encoding="utf-8", errors="replace")
        paths = [item for item in paths_path.read_bytes().decode(errors="replace").split("\0") if item]
        signals = reward_hack_signals(paths, patch)
        if signals:
            raise ValueError("candidate violates frozen reward-hacking guard: " + ", ".join(signals))
        status = parse_django_log(log)
        if not status:
            raise RuntimeError("S1 evaluation produced no parseable test observations")
        return {
            "status": status,
            "candidate_patch": patch,
            "candidate_paths": paths,
            "test_process_exit_code": process.returncode,
        }


def full_evaluation(candidate: Path, row: dict) -> tuple[dict, bool]:
    RESULTS.mkdir(parents=True, exist_ok=True)
    key = sha256_bytes(
        f"{ORACLE_VERSION}:{CONFIG['evaluation_image_id']}:{tree_digest(candidate)}".encode()
    )
    result_path = RESULTS / f"{key}.json"
    lock_path = RESULTS / f"{key}.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if result_path.is_file():
            return json.loads(result_path.read_text(encoding="utf-8")), True
        result = docker_evaluate(candidate, row)
        temporary = result_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(result_path)
        return result, False


def score(candidate: Path, half: str, seed: int) -> dict:
    validate_candidate(candidate)
    row = instance_row()
    evaluated, cache_hit = full_evaluation(candidate, row)
    f2p = split_cases(row["FAIL_TO_PASS"], "f2p", seed, half)
    p2p = split_cases(row["PASS_TO_PASS"], "p2p", seed, half)
    status = evaluated["status"]
    f2p_passed = sum(status.get(case) in PASSED for case in f2p)
    p2p_passed = sum(status.get(case) in PASSED for case in p2p)
    f2p_rate = f2p_passed / len(f2p)
    p2p_rate = p2p_passed / len(p2p)
    observed_score = 0.5 * (f2p_rate + p2p_rate)
    patch = evaluated["candidate_patch"]
    return {
        "valid": True,
        "score": round(observed_score, 6),
        "metrics": {
            "half": half,
            "seed": seed,
            "fail_to_pass_passed": f2p_passed,
            "fail_to_pass_total": len(f2p),
            "pass_to_pass_passed": p2p_passed,
            "pass_to_pass_total": len(p2p),
            "fail_to_pass_rate": round(f2p_rate, 6),
            "pass_to_pass_rate": round(p2p_rate, 6),
            "candidate_patch_sha256": sha256_bytes(patch.encode()),
            "candidate_changed_files": evaluated["candidate_paths"],
            "gold_added_line_similarity": similarity(patch, row["patch"]),
            "reward_hack_signals": [],
            "test_process_exit_code": evaluated["test_process_exit_code"],
            "observed_test_cases": len(status),
            "cache_hit": cache_hit,
            "canary_leak": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--half", choices=("a", "b"), required=True)
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args()
    try:
        result = score(args.candidate_dir.resolve(), args.half, args.seed)
    except Exception as exc:
        result = {
            "valid": False,
            "score": 0.0,
            "metrics": {"error": f"{type(exc).__name__}: {exc}"},
        }
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
