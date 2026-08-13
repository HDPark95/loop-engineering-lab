#!/usr/bin/env python3
"""Confirmatory measurement runner: frozen manifest in, append-only log out.

The smoke path in `se_experiment.py` runs scripted candidates to prove the
apparatus works. This runs the real grid and is built for the one property that
path does not need: a run that dies partway through must be resumable, and every
number in the paper must be recomputable from what it wrote down.

Three things follow from that and each exists because of a specific way the
measurement could be lost or disputed.

**Append-only JSONL, one record per cycle.** Nothing is aggregated at write
time. `replay.py` recomputes every reported quantity from the log alone, so a
reviewer can check our arithmetic without rerunning any agent, and a change to
the analysis never requires new agent calls.

**Resume at trajectory granularity.** A trajectory carries deployed state from
cycle to cycle, so resuming mid-trajectory would mean trusting a snapshot of
that state. Instead an interrupted trajectory is marked abandoned and rerun from
its first cycle, and the abandoned records stay in the log. At six cycles per
trajectory the waste is bounded and the alternative is a silent correctness
hazard.

**Billing and shadow cost stay separate.** Subscription-authenticated prompt
runs incur zero incremental billing. They still record API-price-equivalent
shadow cost for cross-agent comparison, but that estimate is not a spending
gate. An actual dollar ceiling applies only when a manifest explicitly selects
API billing.

Model identity is recorded twice: what the manifest asked for and what the
runtime reported serving. An alias resolves to different weights on different
days, and a measurement taken across days under one alias is a mixture of
models. A mismatch is recorded on the cycle rather than corrected.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from functools import partial
from pathlib import Path

import agent_adapters
import se_experiment

ROOT = Path(__file__).resolve().parent
SCHEMA_VERSION = 2

# Founder-approved lane rule: at most three agent processes at once. Quota or
# rate-limit responses wait and retry; they never trigger a switch to API billing.
MAX_CONCURRENT_AGENTS = 3


class QuotaLimitError(RuntimeError):
    """Adapter signal for a quota or rate-limit response."""

    def __init__(self, message: str, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class TrajectoryRunError(RuntimeError):
    """A post-invocation failure whose usage must survive in the JSONL log."""

    def __init__(self, cause: Exception, failure_record: dict) -> None:
        super().__init__(f"{type(cause).__name__}: {cause}")
        self.cause = cause
        self.failure_record = failure_record


class SharedCycleOneError(RuntimeError):
    """A shared cycle-one invocation failed before usage could be recorded."""


@dataclass(frozen=True)
class TrajectoryKey:
    task: str
    agent: str
    model: str
    cell: str
    seed: int

    def as_dict(self) -> dict:
        return {
            "task": self.task,
            "agent": self.agent,
            "model": self.model,
            "cell": self.cell,
            "seed": self.seed,
        }

    def token(self) -> str:
        return f"{self.task}|{self.agent}|{self.model}|{self.cell}|{self.seed}"


def manifest_digest(manifest: dict) -> str:
    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def finite_nonnegative(value, field: str) -> float:
    """Return a finite non-negative number or reject the manifest."""
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"{field} must be a finite non-negative number") from exc
    if not math.isfinite(number) or number < 0:
        raise SystemExit(f"{field} must be a finite non-negative number")
    return number


def image_is_digest_pinned(image: object) -> bool:
    if not isinstance(image, str):
        return False
    digest_suffix = image.rsplit("@sha256:", 1)[-1] if "@sha256:" in image else ""
    image_id = image.removeprefix("sha256:") if image.startswith("sha256:") else ""
    pinned_digest = digest_suffix or image_id
    return len(pinned_digest) == 64 and all(
        character in "0123456789abcdef" for character in pinned_digest.lower()
    )


def load_manifest(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    missing = [
        field
        for field in (
            "tasks",
            "agents",
            "cells",
            "seeds",
            "cycles",
            "billing_mode",
            "execution_mode",
            "max_concurrent_agents",
            "quota_wait_seconds",
            "quota_max_retries",
            "preregistration_commit",
        )
        if field not in manifest
    ]
    if missing:
        raise SystemExit(f"manifest is missing required fields: {', '.join(missing)}")

    if not manifest["tasks"] or any(task not in se_experiment.TASKS for task in manifest["tasks"]):
        raise SystemExit(f"tasks must be a non-empty subset of {sorted(se_experiment.TASKS)}")
    known_cells = {cell.name for cell in se_experiment.CELLS}
    if not manifest["cells"] or any(cell not in known_cells for cell in manifest["cells"]):
        raise SystemExit(f"cells must be a non-empty subset of {sorted(known_cells)}")
    if len(set(manifest["cells"])) != len(manifest["cells"]):
        raise SystemExit("cells must not contain duplicates")
    try:
        cycles = int(manifest["cycles"])
        seeds = [int(seed) for seed in manifest["seeds"]]
    except (TypeError, ValueError) as exc:
        raise SystemExit("cycles and seeds must be integers") from exc
    if cycles < 1 or not seeds or len(set(seeds)) != len(seeds):
        raise SystemExit("cycles must be positive and seeds must be non-empty and unique")
    if not manifest["agents"]:
        raise SystemExit("agents must be non-empty")
    names = [agent.get("name") for agent in manifest["agents"]]
    if any(not name for name in names) or len(set(names)) != len(names):
        raise SystemExit("agent names must be present and unique")
    if not manifest.get("apparatus_test", False) and not image_is_digest_pinned(
        manifest.get("oracle_container_image")
    ):
        raise SystemExit("oracle_container_image must be pinned by sha256 digest")

    for agent in manifest["agents"]:
        adapter = agent.get("adapter", agent.get("name"))
        if adapter not in {"codex", "claude"} and not manifest.get("apparatus_test", False):
            raise SystemExit(
                f"agent adapter {adapter!r} is not a real confirmatory adapter; "
                "scripted drivers require apparatus_test=true"
            )
        model = agent.get("model", "")
        # An alias is not an identifier. "sonnet" or "session-default" resolves
        # to whatever is current, so a grid measured over several days under one
        # alias silently mixes model versions.
        if not model or model in {"sonnet", "opus", "haiku", "session-default", "default"}:
            raise SystemExit(
                f"agent {agent.get('name')!r} has model {model!r}, which is an alias. "
                "The manifest must pin an immutable model identifier before a "
                "confirmatory run."
            )
        if "usd_per_1k_input" not in agent or "usd_per_1k_output" not in agent:
            raise SystemExit(f"agent {agent.get('name')!r} has no API-equivalent shadow price entry")
        finite_nonnegative(
            agent["usd_per_1k_input"],
            f"agent {agent.get('name')!r} usd_per_1k_input",
        )
        finite_nonnegative(
            agent["usd_per_1k_output"],
            f"agent {agent.get('name')!r} usd_per_1k_output",
        )
        if adapter in {"codex", "claude"}:
            image = agent.get("container_image", "")
            if not image_is_digest_pinned(image):
                raise SystemExit(
                    f"agent {agent.get('name')!r} must pin container_image by sha256 digest"
                )
            timeout = finite_nonnegative(
                agent.get("timeout_seconds", 0),
                f"agent {agent.get('name')!r} timeout_seconds",
            )
            if timeout <= 0:
                raise SystemExit(f"agent {agent.get('name')!r} timeout_seconds must be positive")
            auth_file_env = agent.get("auth_file_env", "")
            if not isinstance(auth_file_env, str) or not auth_file_env:
                raise SystemExit(f"agent {agent.get('name')!r} requires auth_file_env")
            if (
                adapter == "codex"
                and not manifest.get("apparatus_test", False)
                and (
                    not isinstance(agent.get("reasoning_effort"), str)
                    or not agent["reasoning_effort"].strip()
                )
            ):
                raise SystemExit(
                    f"agent {agent.get('name')!r} must freeze reasoning_effort"
                )

    if manifest["billing_mode"] not in {"subscription", "api"}:
        raise SystemExit("billing_mode must be 'subscription' or 'api'")
    if manifest["execution_mode"] != "prompt":
        raise SystemExit("the frozen execution_mode for this study is 'prompt'")
    concurrency = int(manifest["max_concurrent_agents"])
    if concurrency < 1 or concurrency > MAX_CONCURRENT_AGENTS:
        raise SystemExit(f"max_concurrent_agents must be between 1 and {MAX_CONCURRENT_AGENTS}")
    finite_nonnegative(manifest["quota_wait_seconds"], "quota_wait_seconds")
    if int(manifest["quota_max_retries"]) < 0:
        raise SystemExit("quota wait and retry settings must be non-negative")
    if "estimated_api_equivalent_usd_per_trajectory" not in manifest:
        raise SystemExit("manifest requires estimated_api_equivalent_usd_per_trajectory")
    finite_nonnegative(
        manifest["estimated_api_equivalent_usd_per_trajectory"],
        "estimated_api_equivalent_usd_per_trajectory",
    )
    if manifest["billing_mode"] == "api" and "cost_ceiling_usd" not in manifest:
        raise SystemExit("API billing mode requires cost_ceiling_usd")
    if "cost_ceiling_usd" in manifest:
        finite_nonnegative(manifest["cost_ceiling_usd"], "cost_ceiling_usd")
    incremental_billed = finite_nonnegative(
        manifest.get("incremental_billed_usd", 0.0), "incremental_billed_usd"
    )
    if manifest["billing_mode"] == "subscription" and incremental_billed != 0.0:
        raise SystemExit("subscription billing mode must record zero incremental_billed_usd")

    preregistration_commit = manifest["preregistration_commit"]
    if (
        not isinstance(preregistration_commit, str)
        or len(preregistration_commit) not in {40, 64}
        or any(character not in "0123456789abcdef" for character in preregistration_commit.lower())
    ):
        raise SystemExit(
            "preregistration_commit must be a full hexadecimal commit ID frozen "
            "before confirmatory measurement starts."
        )
    return manifest


def trajectories(manifest: dict) -> list[TrajectoryKey]:
    keys = []
    for task in manifest["tasks"]:
        for agent in manifest["agents"]:
            for cell in manifest["cells"]:
                for seed in manifest["seeds"]:
                    keys.append(
                        TrajectoryKey(task, agent["name"], agent["model"], cell, int(seed))
                    )
    return keys


def completed_trajectories(
    log_path: Path, cycles: int, expected_manifest_digest: str | None = None
) -> set[str]:
    """Tokens whose cycles are all present and not marked abandoned.

    A trajectory that stopped partway is not resumed from its last cycle. Its
    records stay in the log, marked, and it is rerun from the start.
    """
    if not log_path.exists():
        return set()
    seen: dict[tuple[str, str], set[int]] = {}
    abandoned: set[tuple[str, str]] = set()
    with log_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                # A truncated final line is what a kill looks like. Skip it; the
                # trajectory it belonged to simply will not reach its cycle count.
                continue
            token = record.get("trajectory")
            if not token:
                continue
            if (
                expected_manifest_digest is not None
                and record.get("manifest_digest") != expected_manifest_digest
            ):
                continue
            attempt = record.get("attempt_id") or record.get("run_id") or "legacy"
            identity = (token, attempt)
            if record.get("abandoned"):
                abandoned.add(identity)
                continue
            if isinstance(record.get("cycle"), int):
                seen.setdefault(identity, set()).add(record["cycle"])
    expected_cycles = set(range(1, cycles + 1))
    return {
        token
        for (token, attempt), cycle_numbers in seen.items()
        if (token, attempt) not in abandoned and cycle_numbers == expected_cycles
    }


def logged_costs(log_path: Path, expected_manifest_digest: str) -> tuple[float, float]:
    """Reconstruct prior shadow and billed usage for this exact manifest."""
    if not log_path.exists():
        return 0.0, 0.0
    shadow = 0.0
    billed = 0.0
    with log_path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get("manifest_digest") != expected_manifest_digest:
                continue
            shadow += float(
                record.get("api_equivalent_usd")
                if record.get("api_equivalent_usd") is not None
                else (record.get("usd") or 0.0)
            )
            billed += float(record.get("incremental_billed_usd") or 0.0)
    return shadow, billed


class CycleLog:
    """Append-only writer. One record per cycle, flushed before it returns."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, record: dict) -> None:
        line = json.dumps(record, sort_keys=True, separators=(",", ":"))
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())


class Budget:
    """Track shadow and billed cost; only API billing has a dollar ceiling."""

    def __init__(
        self,
        billing_mode: str,
        ceiling_usd: float | None,
        prior_shadow_usd: float = 0.0,
        prior_billed_usd: float = 0.0,
    ) -> None:
        self.billing_mode = billing_mode
        self.ceiling = ceiling_usd
        self._shadow_spent = prior_shadow_usd
        self._billed_spent = prior_billed_usd
        self._reserved_billed = 0.0
        self._lock = threading.Lock()

    def add(self, shadow_usd: float) -> None:
        with self._lock:
            self._shadow_spent += shadow_usd
            if self.billing_mode == "api":
                self._billed_spent += shadow_usd

    @property
    def shadow_spent(self) -> float:
        with self._lock:
            return self._shadow_spent

    @property
    def billed_spent(self) -> float:
        with self._lock:
            return self._billed_spent

    def reserve(self, maximum_shadow_usd: float) -> bool:
        """Atomically reserve one trajectory's maximum API-billed amount."""
        with self._lock:
            if self.billing_mode == "subscription":
                return True
            if (
                self._billed_spent + self._reserved_billed + maximum_shadow_usd
                > float(self.ceiling)
            ):
                return False
            self._reserved_billed += maximum_shadow_usd
            return True

    def release(self, maximum_shadow_usd: float) -> None:
        """Release a reservation after its future finishes or is abandoned."""
        if self.billing_mode == "subscription":
            return
        with self._lock:
            self._reserved_billed = max(0.0, self._reserved_billed - maximum_shadow_usd)


class CommonFirstCycleCache:
    """Share the baseline and common cycle-1 execution across factor cells."""

    def __init__(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="loop-eng-common-cycle-")
        self.root = Path(self._temporary.name)
        self._lock = threading.Lock()
        self._futures: dict[str, Future] = {}
        self._baseline_futures: dict[str, Future] = {}
        self._oracle_futures: dict[str, Future] = {}

    def close(self) -> None:
        self._temporary.cleanup()

    def get(
        self,
        key: TrajectoryKey,
        driver,
        manifest: dict,
    ) -> tuple[Path, dict, str, bool]:
        token = f"{key.task}|{key.agent}|{key.model}|{key.seed}"
        with self._lock:
            future = self._futures.get(token)
            owner = future is None
            if owner:
                future = Future()
                self._futures[token] = future
        assert future is not None
        if owner:
            try:
                workspace = self.root / hashlib.sha256(token.encode()).hexdigest()
                se_experiment.copy_seed(key.task, workspace)
                outcome = invoke_with_quota_wait(
                    driver,
                    manifest,
                    model=key.model,
                    task=key.task,
                    workspace=workspace,
                    cycle=1,
                    seed=key.seed,
                    feedback="",
                )
                execution_id = f"common-cycle-1:{uuid.uuid4().hex}"
                future.set_result((workspace, outcome, execution_id))
            except BaseException as exc:
                future.set_exception(exc)
                raise
        workspace, outcome, execution_id = future.result()
        return workspace, outcome, execution_id, owner

    def baseline(self, key: TrajectoryKey) -> tuple[dict, float, dict, float]:
        token = f"{key.task}|{key.seed}"
        with self._lock:
            future = self._baseline_futures.get(token)
            owner = future is None
            if owner:
                future = Future()
                self._baseline_futures[token] = future
        assert future is not None
        if owner:
            try:
                workspace = self.root / f"baseline-{hashlib.sha256(token.encode()).hexdigest()}"
                se_experiment.copy_seed(key.task, workspace)
                result_a, seconds_a = se_experiment.run_oracle(
                    key.task, workspace, "a", key.seed
                )
                result_b, seconds_b = se_experiment.run_oracle(
                    key.task, workspace, "b", key.seed
                )
                future.set_result((result_a, seconds_a, result_b, seconds_b))
            except BaseException as exc:
                future.set_exception(exc)
                raise
        return future.result()

    def grade_cycle_one(
        self, key: TrajectoryKey, candidate_dir: Path
    ) -> tuple[dict, float, dict, float]:
        token = f"{key.task}|{key.agent}|{key.model}|{key.seed}"
        with self._lock:
            future = self._oracle_futures.get(token)
            owner = future is None
            if owner:
                future = Future()
                self._oracle_futures[token] = future
        assert future is not None
        if owner:
            try:
                result_a, seconds_a = se_experiment.run_oracle(
                    key.task, candidate_dir, "a", key.seed
                )
                result_b, seconds_b = se_experiment.run_oracle(
                    key.task, candidate_dir, "b", key.seed
                )
                future.set_result((result_a, seconds_a, result_b, seconds_b))
            except BaseException as exc:
                future.set_exception(exc)
                raise
        return future.result()


def scripted_driver(
    model: str,
    task: str,
    workspace: Path,
    cycle: int,
    seed: int,
    manifest: dict,
    feedback: str = "",
) -> dict:
    """A deterministic stand-in agent, for exercising the runner without an API.

    It is not a research instrument and must never appear in a manifest used for
    a confirmatory run. `load_manifest` permits this adapter only when the
    manifest explicitly carries `apparatus_test=true`.

    The sequence deliberately includes a cosmetic edit that claims improvement,
    because that is the case the grounded and ungrounded gates must treat
    differently, and it is the only behaviour the runner's own tests need.
    """
    from se_experiment import s1_candidates, s3_candidates

    candidates = s1_candidates() if task == "s1" else s3_candidates()
    candidate = candidates[(cycle - 1) % len(candidates)]
    started = time.perf_counter()
    candidate.mutation(workspace)
    return {
        "claim_improved": candidate.claim_improved,
        "self_report": {
            "improved": candidate.claim_improved,
            "confidence": 0.9,
            "evidence": "scripted apparatus mutation",
            "source": "scripted",
        },
        "judge_verdict": None,
        "model_served": model,
        "candidate_digest": digest_of(workspace),
        "input_tokens": candidate.input_tokens,
        "output_tokens": candidate.output_tokens,
        "agent_seconds": round(time.perf_counter() - started, 6),
    }


def real_agent_driver(
    model: str,
    task: str,
    workspace: Path,
    cycle: int,
    seed: int,
    manifest: dict,
    feedback: str = "",
    agent: str = "",
) -> dict:
    entry = next((item for item in manifest["agents"] if item["name"] == agent), None)
    if entry is None:
        raise RuntimeError(f"manifest has no agent entry named {agent!r}")
    adapter = entry.get("adapter", agent)
    auth_env_name = entry["auth_file_env"]
    auth_value = os.environ.get(auth_env_name)
    if not auth_value:
        raise RuntimeError(f"required authentication path variable is unset: {auth_env_name}")
    state_file = None
    state_env_name = entry.get("state_file_env")
    if state_env_name:
        state_value = os.environ.get(state_env_name)
        if not state_value:
            raise RuntimeError(f"required state path variable is unset: {state_env_name}")
        state_file = Path(state_value)
    try:
        outcome = agent_adapters.run_measurement_cycle(
            agent=adapter,
            model=model,
            task=task,
            workspace=workspace,
            cycle=cycle,
            feedback=feedback,
            container_image=entry["container_image"],
            auth_file=Path(auth_value),
            state_file=state_file,
            timeout_seconds=int(entry["timeout_seconds"]),
            billing_mode=manifest["billing_mode"],
            max_budget_usd=float(manifest["estimated_api_equivalent_usd_per_trajectory"]),
            reasoning_effort=entry.get("reasoning_effort"),
        )
    except agent_adapters.AgentInvocationError as exc:
        if exc.kind == "quota":
            raise QuotaLimitError(str(exc)) from exc
        raise
    outcome["candidate_digest"] = digest_of(workspace)
    return outcome


DRIVERS = {"scripted": scripted_driver}


def driver_for(agent: str):
    return DRIVERS.get(agent, partial(real_agent_driver, agent=agent))


def normalize_self_report(outcome: dict) -> dict:
    """Apply the frozen verdict schema; an invalid verdict is a reject."""
    report = outcome.get("self_report")
    valid = isinstance(report, dict)
    improved = report.get("improved") if valid else None
    confidence = report.get("confidence") if valid else None
    evidence = report.get("evidence") if valid else None
    valid = bool(
        valid
        and isinstance(improved, bool)
        and isinstance(confidence, (int, float))
        and not isinstance(confidence, bool)
        and math.isfinite(float(confidence))
        and 0.0 <= float(confidence) <= 1.0
        and isinstance(evidence, str)
        and 1 <= len(evidence.split()) <= 20
    )
    outcome["claim_parsed"] = valid
    outcome["claim_improved"] = bool(improved) if valid else False
    outcome["claim_confidence"] = float(confidence) if valid else None
    outcome["claim_evidence"] = evidence if valid else None
    return outcome


def validate_usage(outcome: dict) -> None:
    for field in ("input_tokens", "output_tokens"):
        value = outcome.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise RuntimeError(f"agent output has invalid {field}")


def invoke_with_quota_wait(driver, manifest: dict, sleep_fn=time.sleep, **kwargs) -> dict:
    """Retry an adapter quota response without changing the billing mode."""
    retries = int(manifest["quota_max_retries"])
    default_wait = float(manifest["quota_wait_seconds"])
    waits: list[float] = []
    for attempt in range(retries + 1):
        try:
            outcome = driver(manifest=manifest, **kwargs)
            validate_usage(outcome)
            normalize_self_report(outcome)
            outcome["quota_wait_events"] = len(waits)
            outcome["quota_wait_seconds"] = round(sum(waits), 3)
            return outcome
        except QuotaLimitError as exc:
            if attempt == retries:
                raise
            delay = default_wait if exc.retry_after_seconds is None else float(exc.retry_after_seconds)
            if not math.isfinite(delay) or delay < 0:
                delay = default_wait
            waits.append(delay)
            sleep_fn(delay)
    raise AssertionError("quota retry loop exhausted without returning or raising")


def digest_of(root: Path) -> str:
    hasher = hashlib.sha256()
    for path in sorted(
        p for p in root.rglob("*") if p.is_file() or p.is_symlink()
    ):
        hasher.update(path.relative_to(root).as_posix().encode())
        if path.is_symlink():
            hasher.update(b"SYMLINK\0")
            hasher.update(os.readlink(path).encode())
        else:
            hasher.update(path.read_bytes())
    return hasher.hexdigest()


def price_of(agent_entry: dict, input_tokens: int, output_tokens: int) -> float:
    return (
        input_tokens / 1000.0 * float(agent_entry["usd_per_1k_input"])
        + output_tokens / 1000.0 * float(agent_entry["usd_per_1k_output"])
    )


def run_trajectory(
    key: TrajectoryKey,
    manifest: dict,
    agent_entry: dict,
    log: CycleLog,
    budget: Budget,
    run_id: str,
    attempt_id: str,
    reserved_maximum_usd: float,
    common_first_cycle: CommonFirstCycleCache,
    common_consumers: int,
) -> dict:
    """Drive one trajectory and write one record per cycle.

    The agent invocation is delegated to the adapter layer. This function owns
    the loop, the gate, the log and the cost accounting, so those stay identical
    across agents.
    """
    driver = driver_for(key.agent)
    cell = next(c for c in se_experiment.CELLS if c.name == key.cell)
    cycles = int(manifest["cycles"])
    trajectory_cost = 0.0
    started = time.time()
    digest = manifest_digest(manifest)

    with tempfile.TemporaryDirectory(prefix="loop-eng-traj-") as workspace_root:
        deployed = Path(workspace_root) / "deployed"
        se_experiment.copy_seed(key.task, deployed)
        baseline_a_result, _, baseline_b_result, _ = common_first_cycle.baseline(key)
        baseline_a = baseline_a_result["score"]
        baseline_b = baseline_b_result["score"]
        deployed_score_a = baseline_a
        deployed_score_b = baseline_b
        feedback = ""

        for cycle in range(1, cycles + 1):
            candidate_dir = Path(workspace_root) / f"candidate-{cycle}"
            if cycle == 1:
                try:
                    common_path, outcome, shared_execution_id, cost_owner = (
                        common_first_cycle.get(key, driver, manifest)
                    )
                except Exception as exc:
                    raise SharedCycleOneError(f"{type(exc).__name__}: {exc}") from exc
                shutil.copytree(common_path, candidate_dir, symlinks=True)
                cost_share = 1.0 / common_consumers
            else:
                shutil.copytree(deployed, candidate_dir, symlinks=True)
                outcome = invoke_with_quota_wait(
                    driver,
                    manifest,
                    model=key.model,
                    task=key.task,
                    workspace=candidate_dir,
                    cycle=cycle,
                    seed=key.seed,
                    feedback=feedback,
                )
                shared_execution_id = None
                cost_owner = True
                cost_share = 1.0
            execution_shadow_usd = price_of(
                agent_entry, outcome["input_tokens"], outcome["output_tokens"]
            )
            shadow_usd = execution_shadow_usd * cost_share
            incremental_billed_usd = shadow_usd if manifest["billing_mode"] == "api" else 0.0
            trajectory_cost += shadow_usd
            if cost_owner:
                budget.add(execution_shadow_usd)
            usage_record = {
                "schema_version": SCHEMA_VERSION,
                "run_id": run_id,
                "attempt_id": attempt_id,
                "manifest_digest": digest,
                "preregistration_commit": manifest["preregistration_commit"],
                "trajectory": key.token(),
                **key.as_dict(),
                "cell_gate_grounded": cell.gate_grounded,
                "cell_feedback": cell.feedback,
                "cycle": cycle,
                "cycles_planned": cycles,
                "model_requested": key.model,
                "model_served": outcome.get("model_served"),
                "model_identity_matches": outcome.get("model_served") == key.model,
                "model_identity_evidence": (
                    outcome.get("model_identity_evidence")
                    or ("runtime_cli_output" if outcome.get("model_served") else "unreported")
                ),
                "model_reroutes": outcome.get("model_reroutes", []),
                "reasoning_effort_requested": agent_entry.get("reasoning_effort"),
                "reasoning_effort_served": outcome.get("reasoning_effort_served"),
                "reasoning_effort_matches": bool(
                    not agent_entry.get("reasoning_effort")
                    or outcome.get("reasoning_effort_served")
                    == agent_entry.get("reasoning_effort")
                ),
                "confirmatory_eligible": bool(
                    not manifest.get("apparatus_test", False)
                    and outcome.get("model_served") == key.model
                    and (
                        not agent_entry.get("reasoning_effort")
                        or outcome.get("reasoning_effort_served")
                        == agent_entry.get("reasoning_effort")
                    )
                ),
                "candidate_digest": outcome.get("candidate_digest"),
                "shared_execution_id": shared_execution_id,
                "cost_allocation_fraction": cost_share,
                "execution_api_equivalent_usd": round(execution_shadow_usd, 6),
                "claim_improved": outcome.get("claim_improved"),
                "claim_confidence": outcome.get("claim_confidence"),
                "claim_evidence": outcome.get("claim_evidence"),
                "claim_parsed": outcome.get("claim_parsed", False),
                "self_report": outcome.get("self_report"),
                "judge_verdict": outcome.get("judge_verdict"),
                "input_tokens": outcome["input_tokens"],
                "output_tokens": outcome["output_tokens"],
                "agent_seconds": outcome.get("agent_seconds"),
                "billing_mode": manifest["billing_mode"],
                "execution_mode": manifest["execution_mode"],
                "apparatus_test": bool(manifest.get("apparatus_test", False)),
                "api_equivalent_usd": round(shadow_usd, 6),
                "incremental_billed_usd": round(incremental_billed_usd, 6),
                "quota_wait_events": outcome.get("quota_wait_events", 0),
                "quota_wait_seconds": outcome.get("quota_wait_seconds", 0.0),
                "wall_clock_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            if trajectory_cost > reserved_maximum_usd + 1e-12:
                cause = RuntimeError(
                    "trajectory usage exceeded estimated_api_equivalent_usd_per_trajectory"
                )
                raise TrajectoryRunError(
                    cause,
                    {
                        **usage_record,
                        "cost_estimate_exceeded": True,
                        "shared_cycle_one_failure": cycle == 1,
                    },
                )
            if (
                not usage_record["model_identity_matches"]
                and not manifest.get("apparatus_test", False)
            ):
                cause = RuntimeError(
                    "runtime did not report the exact immutable model requested by the manifest"
                )
                raise TrajectoryRunError(
                    cause,
                    {**usage_record, "shared_cycle_one_failure": cycle == 1},
                )
            if (
                not usage_record["reasoning_effort_matches"]
                and not manifest.get("apparatus_test", False)
            ):
                cause = RuntimeError(
                    "runtime reasoning effort did not match the frozen manifest"
                )
                raise TrajectoryRunError(
                    cause,
                    {**usage_record, "shared_cycle_one_failure": cycle == 1},
                )

            try:
                if cycle == 1:
                    try:
                        (
                            oracle_a_result,
                            oracle_a_seconds,
                            oracle_b_result,
                            oracle_b_seconds,
                        ) = common_first_cycle.grade_cycle_one(key, candidate_dir)
                    except Exception as exc:
                        raise TrajectoryRunError(
                            exc,
                            {**usage_record, "shared_cycle_one_failure": True},
                        ) from exc
                    oracle_time_share = cost_share
                else:
                    oracle_a_result, oracle_a_seconds = se_experiment.run_oracle(
                        key.task, candidate_dir, "a", key.seed
                    )
                    oracle_b_result, oracle_b_seconds = se_experiment.run_oracle(
                        key.task, candidate_dir, "b", key.seed
                    )
                    oracle_time_share = 1.0
                oracle_a = oracle_a_result["score"]
                oracle_b = oracle_b_result["score"]
                delta_a = oracle_a - deployed_score_a
                delta_b = oracle_b - deployed_score_b
                accepted = se_experiment.gate_decision(
                    cell, outcome["claim_improved"], delta_a
                )
                if accepted:
                    # Promotion is what "deployed" means. A rejected candidate is
                    # discarded and the next cycle starts from the last accepted state.
                    shutil.rmtree(deployed)
                    shutil.copytree(candidate_dir, deployed, symlinks=True)
                    deployed_score_a = oracle_a
                    deployed_score_b = oracle_b
                feedback = se_experiment.feedback_text(
                    cell, accepted, delta_a, oracle_a
                )

                log.write(
                    {
                        **usage_record,
                        "oracle_score_hoa": oracle_a,
                        "oracle_score_hob": oracle_b,
                        "delta_hoa": delta_a,
                        "delta_hob": delta_b,
                        "oracle_score": oracle_b,
                        "oracle_delta": delta_b,
                        "accepted": accepted,
                        "deployed_score_hoa": deployed_score_a,
                        "deployed_score_hob": deployed_score_b,
                        "deployed_score": deployed_score_b,
                        "baseline_score_hoa": baseline_a,
                        "baseline_score_hob": baseline_b,
                        "baseline_score": baseline_b,
                        "feedback_to_next_cycle": feedback,
                        "canary_leak": bool(
                            oracle_a_result.get("metrics", {}).get("canary_leak")
                            or oracle_b_result.get("metrics", {}).get("canary_leak")
                        ),
                        "oracle_valid": bool(
                            oracle_a_result.get("valid") and oracle_b_result.get("valid")
                        ),
                        "oracle_seconds": round(
                            (oracle_a_seconds + oracle_b_seconds) * oracle_time_share, 6
                        ),
                    }
                )
            except TrajectoryRunError:
                raise
            except Exception as exc:
                raise TrajectoryRunError(exc, usage_record) from exc

    return {
        "trajectory": key.token(),
        "attempt_id": attempt_id,
        "api_equivalent_usd": round(trajectory_cost, 6),
        "incremental_billed_usd": (
            round(trajectory_cost, 6) if manifest["billing_mode"] == "api" else 0.0
        ),
        "seconds": round(time.time() - started, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="print the grid, the resume position and the cost estimate, then stop",
    )
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    if manifest.get("oracle_container_image"):
        os.environ["LOOP_ORACLE_IMAGE"] = manifest["oracle_container_image"]
    cycles = int(manifest["cycles"])
    everything = trajectories(manifest)
    common_groups_total = {
        (key.task, key.agent, key.model, key.seed) for key in everything
    }
    logical_cycle_rows = len(everything) * cycles
    unique_agent_executions = len(common_groups_total) + len(everything) * max(0, cycles - 1)
    digest = manifest_digest(manifest)
    done = completed_trajectories(args.log, cycles, digest)
    todo = [key for key in everything if key.token() not in done]

    estimate_per_trajectory = float(manifest["estimated_api_equivalent_usd_per_trajectory"])
    total_shadow_estimate = estimate_per_trajectory * len(todo)
    billing_mode = manifest["billing_mode"]
    total_billed_estimate = total_shadow_estimate if billing_mode == "api" else 0.0
    ceiling = float(manifest["cost_ceiling_usd"]) if billing_mode == "api" else None
    prior_shadow, prior_billed = logged_costs(args.log, digest)
    within_ceiling = ceiling is None or prior_billed + total_billed_estimate <= ceiling
    concurrency = int(manifest["max_concurrent_agents"])

    print(
        json.dumps(
            {
                "run_id": args.run_id,
                "manifest_digest": digest,
                "trajectories_total": len(everything),
                "trajectories_complete": len(done),
                "trajectories_remaining": len(todo),
                "cycles_per_trajectory": cycles,
                "logical_cycle_rows": logical_cycle_rows,
                "unique_agent_executions": unique_agent_executions,
                "billing_mode": billing_mode,
                "execution_mode": manifest["execution_mode"],
                "apparatus_test": bool(manifest.get("apparatus_test", False)),
                "estimated_api_equivalent_usd_remaining": round(total_shadow_estimate, 2),
                "estimated_incremental_billed_usd_remaining": round(total_billed_estimate, 2),
                "prior_api_equivalent_usd": round(prior_shadow, 6),
                "prior_incremental_billed_usd": round(prior_billed, 6),
                "cost_ceiling_usd": ceiling,
                "within_ceiling": within_ceiling,
                "max_concurrent_agents": concurrency,
                "quota_wait_seconds": float(manifest["quota_wait_seconds"]),
                "quota_max_retries": int(manifest["quota_max_retries"]),
            },
            indent=2,
            sort_keys=True,
        )
    )
    if args.plan_only:
        return 0
    if not within_ceiling:
        print(
            "refusing to start: the remaining grid is estimated above the ceiling. "
            "Raise the ceiling deliberately or cut the grid; do not discover this "
            "halfway through.",
            file=sys.stderr,
        )
        return 2

    log = CycleLog(args.log)
    budget = Budget(billing_mode, ceiling, prior_shadow, prior_billed)
    agents_by_name = {entry["name"]: entry for entry in manifest["agents"]}
    stopped_for_budget = []
    abandoned_attempts = 0
    common_consumer_counts: dict[tuple[str, str, str, int], int] = {}
    common_members: dict[tuple[str, str, str, int], list[TrajectoryKey]] = {}
    for key in todo:
        group = (key.task, key.agent, key.model, key.seed)
        common_consumer_counts[group] = common_consumer_counts.get(group, 0) + 1
        common_members.setdefault(group, []).append(key)
    attempt_ids = {
        group: f"{args.run_id}:{uuid.uuid4().hex}" for group in common_members
    }
    common_first_cycle = CommonFirstCycleCache()
    failed_shared_cycle_one_groups: set[tuple[str, str, str, int]] = set()
    individually_abandoned: set[str] = set()

    try:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {}
            if not budget.reserve(total_shadow_estimate):
                stopped_for_budget.extend(key.token() for key in todo)
            for key in ([] if stopped_for_budget else todo):
                group = (key.task, key.agent, key.model, key.seed)
                attempt_id = attempt_ids[group]
                futures[
                    pool.submit(
                        run_trajectory,
                        key,
                        manifest,
                        agents_by_name[key.agent],
                        log,
                        budget,
                        args.run_id,
                        attempt_id,
                        estimate_per_trajectory,
                        common_first_cycle,
                        common_consumer_counts[group],
                    )
                ] = (key, group, attempt_id, estimate_per_trajectory)
            for future in as_completed(futures):
                key, group, attempt_id, reservation = futures[future]
                try:
                    result = future.result()
                except TrajectoryRunError as exc:
                    abandoned_attempts += 1
                    if exc.failure_record.get("shared_cycle_one_failure"):
                        failed_shared_cycle_one_groups.add(group)
                    individually_abandoned.add(key.token())
                    log.write(
                        {
                            **exc.failure_record,
                            "abandoned": True,
                            "error": f"{type(exc.cause).__name__}: {exc.cause}",
                        }
                    )
                    print(f"abandoned {key.token()}: {type(exc.cause).__name__}", file=sys.stderr)
                    continue
                except Exception as exc:  # noqa: BLE001 - one trajectory must not end the run
                    abandoned_attempts += 1
                    if isinstance(exc, SharedCycleOneError):
                        failed_shared_cycle_one_groups.add(group)
                    individually_abandoned.add(key.token())
                    log.write(
                        {
                            "schema_version": SCHEMA_VERSION,
                            "run_id": args.run_id,
                            "attempt_id": attempt_id,
                            "manifest_digest": digest,
                            "preregistration_commit": manifest["preregistration_commit"],
                            "trajectory": key.token(),
                            **key.as_dict(),
                            "abandoned": True,
                            "error": f"{type(exc).__name__}: {exc}",
                            "wall_clock_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        }
                    )
                    print(f"abandoned {key.token()}: {type(exc).__name__}", file=sys.stderr)
                    continue
                finally:
                    budget.release(reservation)
                print(json.dumps(result, sort_keys=True))
            for group in failed_shared_cycle_one_groups:
                for key in common_members[group]:
                    if key.token() in individually_abandoned:
                        continue
                    abandoned_attempts += 1
                    log.write(
                        {
                            "schema_version": SCHEMA_VERSION,
                            "run_id": args.run_id,
                            "attempt_id": attempt_ids[group],
                            "manifest_digest": digest,
                            "preregistration_commit": manifest["preregistration_commit"],
                            "trajectory": key.token(),
                            **key.as_dict(),
                            "abandoned": True,
                            "bundle_abandoned": True,
                            "error": "common cycle-1 bundle peer failed",
                            "wall_clock_utc": time.strftime(
                                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                            ),
                        }
                    )
    finally:
        common_first_cycle.close()

    summary = {
        "run_id": args.run_id,
        "billing_mode": billing_mode,
        "api_equivalent_usd": round(budget.shadow_spent, 4),
        "incremental_billed_usd": round(budget.billed_spent, 4),
        "cost_ceiling_usd": ceiling,
        "trajectories_not_started_for_budget": len(stopped_for_budget),
        "abandoned_attempts": abandoned_attempts,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    if stopped_for_budget:
        # Say it rather than let a short grid look like a complete one.
        print(
            f"{len(stopped_for_budget)} trajectories were not started because the "
            "ceiling would have been crossed. The grid is incomplete and must be "
            "reported as such.",
            file=sys.stderr,
        )
        return 3
    if abandoned_attempts:
        print(
            f"{abandoned_attempts} trajectory attempt(s) were abandoned. The grid "
            "is incomplete until those trajectories succeed on a later attempt.",
            file=sys.stderr,
        )
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
