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
from contextlib import ExitStack
import hashlib
import json
import math
import os
import shutil
import stat
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
SCHEMA_VERSION = 5

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


def verify_isolation_preflight(manifest_path: Path, manifest: dict) -> None:
    """Bind a confirmatory manifest to a successful sandbox isolation record."""
    relative = manifest.get("isolation_preflight_record")
    expected_digest = manifest.get("isolation_preflight_sha256")
    if not isinstance(relative, str) or not relative.strip():
        raise SystemExit("confirmatory manifest requires isolation_preflight_record")
    if (
        not isinstance(expected_digest, str)
        or len(expected_digest) != 64
        or any(character not in "0123456789abcdef" for character in expected_digest.lower())
    ):
        raise SystemExit("confirmatory manifest requires isolation_preflight_sha256")
    root = manifest_path.resolve().parent
    record_path = (root / relative).resolve()
    try:
        record_path.relative_to(root)
    except ValueError as exc:
        raise SystemExit("isolation preflight record must stay inside the frozen repository") from exc
    if not record_path.is_file():
        raise SystemExit("isolation preflight record is missing")
    payload = record_path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected_digest.lower():
        raise SystemExit("isolation preflight record digest does not match the manifest")
    try:
        record = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SystemExit("isolation preflight record is not valid JSON") from exc
    if not record.get("passed", False):
        raise SystemExit("isolation preflight did not pass")
    image = manifest["candidate_sandbox_image"]
    if record.get("sandbox_image_requested") != image:
        raise SystemExit("isolation preflight was run against a different sandbox image")
    if record.get("sandbox_image_resolved") != image:
        raise SystemExit("isolation preflight did not resolve to the frozen sandbox image")


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
        manifest.get("candidate_sandbox_image")
    ):
        raise SystemExit("candidate_sandbox_image must be pinned by sha256 digest")
    if not manifest.get("apparatus_test", False):
        archive_dir = manifest.get("artifact_archive_dir")
        if not isinstance(archive_dir, str) or not archive_dir.strip():
            raise SystemExit("confirmatory manifests require artifact_archive_dir")

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
        if not manifest.get("apparatus_test", False):
            required_pricing_fields = (
                "usd_per_1k_cached_input",
                "cache_write_input_multiplier",
                "cache_write_1h_input_multiplier",
                "long_context_threshold_input_tokens",
                "long_context_input_multiplier",
                "long_context_output_multiplier",
                "pricing_schedule_id",
                "pricing_source_url",
                "pricing_retrieved_utc",
            )
            missing_pricing = [
                field for field in required_pricing_fields if field not in agent
            ]
            if missing_pricing:
                raise SystemExit(
                    f"agent {agent.get('name')!r} is missing frozen shadow-pricing "
                    f"fields: {', '.join(missing_pricing)}"
                )
            finite_nonnegative(
                agent["usd_per_1k_cached_input"],
                f"agent {agent.get('name')!r} usd_per_1k_cached_input",
            )
            cache_write_multiplier = finite_nonnegative(
                agent["cache_write_input_multiplier"],
                f"agent {agent.get('name')!r} cache_write_input_multiplier",
            )
            long_input_multiplier = finite_nonnegative(
                agent["long_context_input_multiplier"],
                f"agent {agent.get('name')!r} long_context_input_multiplier",
            )
            cache_write_1h_multiplier = finite_nonnegative(
                agent["cache_write_1h_input_multiplier"],
                f"agent {agent.get('name')!r} cache_write_1h_input_multiplier",
            )
            long_output_multiplier = finite_nonnegative(
                agent["long_context_output_multiplier"],
                f"agent {agent.get('name')!r} long_context_output_multiplier",
            )
            threshold = agent["long_context_threshold_input_tokens"]
            if (
                not isinstance(threshold, int)
                or isinstance(threshold, bool)
                or threshold <= 0
            ):
                raise SystemExit(
                    f"agent {agent.get('name')!r} long-context threshold must be "
                    "a positive integer"
                )
            if min(
                cache_write_multiplier,
                cache_write_1h_multiplier,
                long_input_multiplier,
                long_output_multiplier,
            ) < 1.0:
                raise SystemExit("shadow-pricing multipliers must be at least one")
            for field in ("pricing_schedule_id", "pricing_source_url", "pricing_retrieved_utc"):
                if not isinstance(agent[field], str) or not agent[field].strip():
                    raise SystemExit(
                        f"agent {agent.get('name')!r} {field} must be a nonempty string"
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
            if (
                adapter in {"codex", "claude"}
                and manifest.get("billing_mode") == "subscription"
                and not manifest.get("apparatus_test", False)
                and agent.get("persist_refreshed_credentials") is not True
            ):
                raise SystemExit(
                    f"agent {agent.get('name')!r} must enable serialized OAuth "
                    "credential refresh persistence"
                )
            if adapter == "claude" and "state_file_env" in agent:
                raise SystemExit(
                    f"agent {agent.get('name')!r} must not expose an external Claude "
                    "state file; the adapter generates sanitized state per call"
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
    if not manifest.get("apparatus_test", False):
        expected_tasks = {"s1_swebench", "s3", "g1", "b1"}
        expected_cells = {cell.name for cell in se_experiment.CELLS}
        expected_seeds = {11, 23, 37, 53, 71}
        adapters = [agent.get("adapter", agent.get("name")) for agent in manifest["agents"]]
        if set(manifest["tasks"]) != expected_tasks or len(manifest["tasks"]) != 4:
            raise SystemExit(
                "confirmatory task grid must contain exactly s1_swebench, s3, g1, and b1"
            )
        if set(manifest["cells"]) != expected_cells or len(manifest["cells"]) != 4:
            raise SystemExit("confirmatory grid must contain exactly the four frozen cells")
        if set(seeds) != expected_seeds or len(seeds) != 5:
            raise SystemExit("confirmatory grid must use exactly seeds 11, 23, 37, 53, and 71")
        if cycles != 6:
            raise SystemExit("confirmatory trajectories must contain exactly six cycles")
        if set(adapters) != {"codex", "claude"} or len(adapters) != 2:
            raise SystemExit("confirmatory grid requires exactly one Codex and one Claude agent")
        if manifest["billing_mode"] != "subscription":
            raise SystemExit("confirmatory billing_mode is frozen to subscription")
        if (
            not isinstance(manifest.get("cell_schedule_seed"), str)
            or not manifest["cell_schedule_seed"].strip()
        ):
            raise SystemExit("confirmatory grid requires a frozen cell_schedule_seed")
        verify_isolation_preflight(path, manifest)
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


def common_group(key: TrajectoryKey) -> tuple[str, str, str, int]:
    """Identity of the four branches that share one cycle-one execution."""
    return key.task, key.agent, key.model, key.seed


def cell_order(manifest: dict, key: TrajectoryKey) -> list[str]:
    """Return the frozen pseudorandom treatment order within one block."""
    schedule_seed = manifest.get("cell_schedule_seed", "apparatus-cell-order-v1")
    prefix = f"{schedule_seed}|{key.task}|{key.agent}|{key.model}|{key.seed}|"
    return sorted(
        manifest["cells"],
        key=lambda cell: hashlib.sha256(f"{prefix}{cell}".encode()).digest(),
    )


def scheduled_trajectories(keys: list[TrajectoryKey], manifest: dict) -> list[TrajectoryKey]:
    """Submit one randomized branch per block before any second branch.

    The first round owns and completes each common cycle-one future. Later
    rounds therefore reuse it without occupying worker slots while waiting.
    Hash-ranked cell order prevents a fixed treatment arm from always running
    first within every task-agent-seed block.
    """
    members: dict[tuple[str, str, str, int], list[TrajectoryKey]] = {}
    for key in keys:
        members.setdefault(common_group(key), []).append(key)
    ordered_members = {
        group: sorted(
            group_keys,
            key=lambda key: cell_order(manifest, key).index(key.cell),
        )
        for group, group_keys in members.items()
    }
    schedule = []
    for position in range(max((len(group) for group in ordered_members.values()), default=0)):
        for group in sorted(ordered_members):
            if position < len(ordered_members[group]):
                schedule.append(ordered_members[group][position])
    return schedule


def worker_lane_limits(manifest: dict) -> dict[str, int]:
    """Give every rotating subscription credential exactly one writer lane."""
    concurrency = int(manifest["max_concurrent_agents"])
    serialized = sorted(
        {
            entry.get("adapter", entry.get("name"))
            for entry in manifest["agents"]
            if entry.get("persist_refreshed_credentials") is True
        }
    )
    if not serialized or concurrency < 2:
        return {"shared": concurrency}
    limits = {adapter: 1 for adapter in serialized}
    nonserialized = any(
        entry.get("adapter", entry.get("name")) not in limits
        for entry in manifest["agents"]
    )
    remaining = concurrency - len(limits)
    if nonserialized and remaining > 0:
        limits["other"] = remaining
    return limits


def common_attempt_states(
    log_path: Path, expected_manifest_digest: str
) -> dict[tuple[tuple[str, str, str, int], str], dict]:
    """Read per-attempt block state for group-safe resume decisions."""
    states: dict[tuple[tuple[str, str, str, int], str], dict] = {}
    if not log_path.exists():
        return states
    with log_path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get("manifest_digest") != expected_manifest_digest:
                continue
            required = ("task", "agent", "model", "seed", "trajectory")
            if any(record.get(field) is None for field in required):
                continue
            group = (
                record["task"],
                record["agent"],
                record["model"],
                int(record["seed"]),
            )
            attempt = record.get("attempt_id") or record.get("run_id") or "legacy"
            state = states.setdefault(
                (group, attempt),
                {"cycles": {}, "abandoned": set(), "heads": {}},
            )
            token = record["trajectory"]
            state["heads"].setdefault(token, record)
            if record.get("abandoned"):
                state["abandoned"].add(token)
            elif isinstance(record.get("cycle"), int):
                state["cycles"].setdefault(token, set()).add(record["cycle"])
    return states


def expected_common_groups(manifest: dict) -> dict[tuple[str, str, str, int], set[str]]:
    expected: dict[tuple[str, str, str, int], set[str]] = {}
    for key in trajectories(manifest):
        expected.setdefault(common_group(key), set()).add(key.token())
    return expected


def completed_common_group_trajectories(
    log_path: Path,
    manifest: dict,
    cycles: int,
    expected_manifest_digest: str,
) -> set[str]:
    """Count a block complete only when one attempt completed every branch."""
    expected = expected_common_groups(manifest)
    expected_cycles = set(range(1, cycles + 1))
    complete = set()
    for (group, _attempt), state in common_attempt_states(
        log_path, expected_manifest_digest
    ).items():
        tokens = expected.get(group, set())
        if tokens and not state["abandoned"] and all(
            state["cycles"].get(token) == expected_cycles for token in tokens
        ):
            complete.update(tokens)
    return complete


def incomplete_common_attempt_markers(
    log_path: Path,
    manifest: dict,
    cycles: int,
    expected_manifest_digest: str,
) -> list[dict]:
    """Create append-only tombstones for partial common-cycle block attempts."""
    expected = expected_common_groups(manifest)
    expected_cycles = set(range(1, cycles + 1))
    markers = []
    for (group, attempt), state in common_attempt_states(
        log_path, expected_manifest_digest
    ).items():
        tokens = expected.get(group, set())
        is_complete = bool(tokens) and not state["abandoned"] and all(
            state["cycles"].get(token) == expected_cycles for token in tokens
        )
        if is_complete:
            continue
        for token in sorted(state["cycles"]):
            if token in state["abandoned"]:
                continue
            head = state["heads"][token]
            markers.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "run_id": head.get("run_id"),
                    "attempt_id": attempt,
                    "manifest_digest": expected_manifest_digest,
                    "preregistration_commit": manifest["preregistration_commit"],
                    "trajectory": token,
                    "task": head["task"],
                    "agent": head["agent"],
                    "model": head["model"],
                    "cell": head.get("cell"),
                    "seed": head["seed"],
                    "abandoned": True,
                    "bundle_abandoned": True,
                    "reconciled_incomplete_common_group": True,
                    "error": "incomplete common-cycle block attempt",
                    "wall_clock_utc": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                    ),
                }
            )
    return markers


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


def archive_candidate(candidate: Path, archive_root: Path) -> str:
    """Store an exact content-addressed candidate without duplicating seed files."""
    objects = archive_root / "objects"
    manifests = archive_root / "manifests"
    objects.mkdir(parents=True, exist_ok=True)
    manifests.mkdir(parents=True, exist_ok=True)
    root = candidate.resolve()
    entries = []
    for path in sorted(candidate.rglob("*")):
        relative = path.relative_to(candidate).as_posix()
        mode = stat.S_IMODE(path.lstat().st_mode)
        if path.is_symlink():
            target = os.readlink(path)
            try:
                if not path.resolve(strict=True).is_relative_to(root):
                    raise RuntimeError(f"candidate symlink escapes workspace: {relative}")
            except (OSError, RuntimeError) as exc:
                raise RuntimeError(f"unsafe candidate symlink: {relative}") from exc
            entries.append({"path": relative, "type": "symlink", "target": target, "mode": mode})
            continue
        if path.is_dir():
            entries.append({"path": relative, "type": "directory", "mode": mode})
            continue
        if not path.is_file():
            raise RuntimeError(f"unsupported candidate entry: {relative}")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        object_id = digest.hexdigest()
        object_dir = objects / object_id[:2]
        object_dir.mkdir(parents=True, exist_ok=True)
        object_path = object_dir / object_id
        if not object_path.exists():
            try:
                descriptor = os.open(
                    object_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444
                )
            except FileExistsError:
                descriptor = None
            if descriptor is not None:
                try:
                    with os.fdopen(descriptor, "wb") as output, path.open("rb") as source:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                        output.flush()
                        os.fsync(output.fileno())
                except Exception:
                    object_path.unlink(missing_ok=True)
                    raise
        entries.append(
            {
                "path": relative,
                "type": "file",
                "sha256": object_id,
                "size": path.stat().st_size,
                "mode": mode,
            }
        )
    record = {"schema_version": 1, "entries": entries}
    payload = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    manifest_id = hashlib.sha256(payload).hexdigest()
    manifest_path = manifests / f"{manifest_id}.json"
    if manifest_path.exists():
        if manifest_path.read_bytes() != payload + b"\n":
            raise RuntimeError("artifact archive manifest digest collision")
    else:
        try:
            descriptor = os.open(
                manifest_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444
            )
        except FileExistsError:
            descriptor = None
        if descriptor is not None:
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload + b"\n")
                output.flush()
                os.fsync(output.fileno())
    return manifest_id


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
            persist_refreshed_credentials=bool(
                entry.get("persist_refreshed_credentials", False)
            ),
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
    total_input = outcome["input_tokens"]
    cached = outcome.get("cached_input_tokens", 0)
    uncached = outcome.get("uncached_input_tokens", total_input - cached)
    cache_write = outcome.get("cache_write_input_tokens", 0)
    for field, value in (
        ("cached_input_tokens", cached),
        ("uncached_input_tokens", uncached),
        ("cache_write_input_tokens", cache_write),
        ("cache_write_5m_input_tokens", outcome.get("cache_write_5m_input_tokens", 0)),
        ("cache_write_1h_input_tokens", outcome.get("cache_write_1h_input_tokens", 0)),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise RuntimeError(f"agent output has invalid {field}")
    if cached + uncached != total_input or cache_write > uncached:
        raise RuntimeError("agent output has inconsistent normalized input usage")
    if outcome.get("cache_write_input_tokens_exact", False) and (
        outcome.get("cache_write_5m_input_tokens", 0)
        + outcome.get("cache_write_1h_input_tokens", 0)
        != cache_write
    ):
        raise RuntimeError("agent output has inconsistent cache-write TTL usage")
    request_usages = outcome.get("request_usages", [])
    if not isinstance(request_usages, list):
        raise RuntimeError("agent output has invalid request_usages")
    for request in request_usages:
        if not isinstance(request, dict):
            raise RuntimeError("agent output has invalid request usage entry")
        for field in ("input_tokens", "cached_input_tokens", "output_tokens"):
            value = request.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise RuntimeError(f"agent request usage has invalid {field}")
        if request["cached_input_tokens"] > request["input_tokens"]:
            raise RuntimeError("agent request cached input exceeds total input")


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


def price_schedule(agent_entry: dict) -> dict:
    """Return the complete frozen schedule copied into every schema-five row."""
    return {
        "pricing_schedule_id": agent_entry.get("pricing_schedule_id", "legacy-apparatus"),
        "pricing_source_url": agent_entry.get("pricing_source_url", "apparatus-only"),
        "pricing_retrieved_utc": agent_entry.get("pricing_retrieved_utc", "unregistered"),
        "usd_per_1k_input": float(agent_entry["usd_per_1k_input"]),
        "usd_per_1k_cached_input": float(
            agent_entry.get("usd_per_1k_cached_input", agent_entry["usd_per_1k_input"])
        ),
        "usd_per_1k_output": float(agent_entry["usd_per_1k_output"]),
        "cache_write_input_multiplier": float(
            agent_entry.get("cache_write_input_multiplier", 1.0)
        ),
        "cache_write_1h_input_multiplier": float(
            agent_entry.get("cache_write_1h_input_multiplier", 1.0)
        ),
        "long_context_threshold_input_tokens": int(
            agent_entry.get("long_context_threshold_input_tokens", 10**18)
        ),
        "long_context_input_multiplier": float(
            agent_entry.get("long_context_input_multiplier", 1.0)
        ),
        "long_context_output_multiplier": float(
            agent_entry.get("long_context_output_multiplier", 1.0)
        ),
    }


def price_of(agent_entry: dict, outcome: dict) -> dict:
    """Compute a reproducible API-equivalent interval from normalized usage.

    Codex exposes cache reads but not cache writes, so its upper endpoint treats
    every non-cached input token as a possible cache write. The lower endpoint
    treats none as a write. Claude exposes cache creation separately, making
    the endpoints equal. Long-context premiums are classified per model request
    when request-level telemetry exists; an unclassifiable aggregate fails.
    """
    schedule = price_schedule(agent_entry)
    total_input = int(outcome["input_tokens"])
    output_tokens = int(outcome["output_tokens"])
    cached = int(outcome.get("cached_input_tokens", 0))
    uncached = int(outcome.get("uncached_input_tokens", total_input - cached))
    cache_write = int(outcome.get("cache_write_input_tokens", 0))
    cache_write_5m = int(outcome.get("cache_write_5m_input_tokens", 0))
    cache_write_1h = int(outcome.get("cache_write_1h_input_tokens", 0))
    cache_write_exact = bool(outcome.get("cache_write_input_tokens_exact", False))
    requests = outcome.get("request_usages", [])
    threshold = schedule["long_context_threshold_input_tokens"]

    if requests:
        if sum(request["input_tokens"] for request in requests) != total_input:
            raise RuntimeError("request input usage does not sum to total input usage")
        if sum(request["cached_input_tokens"] for request in requests) != cached:
            raise RuntimeError("request cached usage does not sum to total cached usage")
        if sum(request["output_tokens"] for request in requests) != output_tokens:
            raise RuntimeError("request output usage does not sum to total output usage")
        standard = [request for request in requests if request["input_tokens"] <= threshold]
        long = [request for request in requests if request["input_tokens"] > threshold]
    else:
        if total_input > threshold:
            raise RuntimeError(
                "aggregate usage exceeds the long-context threshold without request telemetry"
            )
        standard = [{
            "input_tokens": total_input,
            "cached_input_tokens": cached,
            "output_tokens": output_tokens,
        }]
        long = []

    def totals(rows: list[dict]) -> tuple[int, int, int]:
        input_total = sum(row["input_tokens"] for row in rows)
        cached_total = sum(row["cached_input_tokens"] for row in rows)
        output_total = sum(row["output_tokens"] for row in rows)
        return input_total - cached_total, cached_total, output_total

    standard_uncached, standard_cached, standard_output = totals(standard)
    long_uncached, long_cached, long_output = totals(long)
    if standard_uncached + long_uncached != uncached:
        raise RuntimeError("normalized uncached usage disagrees with request telemetry")

    input_rate = schedule["usd_per_1k_input"] / 1000.0
    cached_rate = schedule["usd_per_1k_cached_input"] / 1000.0
    output_rate = schedule["usd_per_1k_output"] / 1000.0
    write_multiplier = schedule["cache_write_input_multiplier"]
    write_1h_multiplier = schedule["cache_write_1h_input_multiplier"]
    long_input_multiplier = schedule["long_context_input_multiplier"]
    long_output_multiplier = schedule["long_context_output_multiplier"]

    base_without_writes = (
        standard_uncached * input_rate
        + standard_cached * cached_rate
        + standard_output * output_rate
        + long_uncached * input_rate * long_input_multiplier
        + long_cached * cached_rate * long_input_multiplier
        + long_output * output_rate * long_output_multiplier
    )
    if cache_write_exact:
        if cache_write and standard_uncached and long_uncached:
            raise RuntimeError("cache writes cannot be allocated across mixed context tiers")
        write_tier_multiplier = long_input_multiplier if long_uncached else 1.0
        write_surcharge = input_rate * write_tier_multiplier * (
            cache_write_5m * (write_multiplier - 1.0)
            + cache_write_1h * (write_1h_multiplier - 1.0)
        )
        lower = upper = base_without_writes + write_surcharge
    else:
        lower = base_without_writes
        upper = base_without_writes + (
            standard_uncached * input_rate * (write_multiplier - 1.0)
            + long_uncached
            * input_rate
            * long_input_multiplier
            * (write_multiplier - 1.0)
        )
    return {
        "lower_usd": round(lower, 9),
        "upper_usd": round(upper, 9),
        "exact": abs(upper - lower) <= 1e-12,
        "cache_write_input_tokens": cache_write,
        "cache_write_5m_input_tokens": cache_write_5m,
        "cache_write_1h_input_tokens": cache_write_1h,
        "cache_write_input_tokens_exact": cache_write_exact,
        "standard_uncached_input_tokens": standard_uncached,
        "standard_cached_input_tokens": standard_cached,
        "standard_output_tokens": standard_output,
        "long_uncached_input_tokens": long_uncached,
        "long_cached_input_tokens": long_cached,
        "long_output_tokens": long_output,
        "request_count": len(requests) if requests else 1,
        "schedule": schedule,
    }


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
    archive_root: Path | None,
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
            received_digest = digest_of(deployed)
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
            pricing = price_of(agent_entry, outcome)
            execution_shadow_usd = pricing["upper_usd"]
            execution_shadow_lower_usd = pricing["lower_usd"]
            shadow_usd = execution_shadow_usd * cost_share
            shadow_lower_usd = execution_shadow_lower_usd * cost_share
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
                "cell_schedule_seed": manifest.get(
                    "cell_schedule_seed", "apparatus-cell-order-v1"
                ),
                "cell_schedule_position": cell_order(manifest, key).index(key.cell) + 1,
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
                    and outcome.get("credential_leak_scan_passed") is True
                    and (
                        not agent_entry.get("reasoning_effort")
                        or outcome.get("reasoning_effort_served")
                        == agent_entry.get("reasoning_effort")
                    )
                ),
                "candidate_digest": outcome.get("candidate_digest"),
                "candidate_changed": outcome.get("candidate_digest") != received_digest,
                "agent_completed": True,
                "edit_success": outcome.get("candidate_digest") != received_digest,
                "shared_execution_id": shared_execution_id,
                "cost_allocation_fraction": cost_share,
                "execution_api_equivalent_usd": round(execution_shadow_usd, 6),
                "execution_api_equivalent_usd_lower_bound": round(
                    execution_shadow_lower_usd, 6
                ),
                "execution_input_tokens": outcome["input_tokens"],
                "execution_uncached_input_tokens": (
                    pricing["standard_uncached_input_tokens"]
                    + pricing["long_uncached_input_tokens"]
                ),
                "execution_cached_input_tokens": (
                    pricing["standard_cached_input_tokens"]
                    + pricing["long_cached_input_tokens"]
                ),
                "execution_cache_write_input_tokens": pricing[
                    "cache_write_input_tokens"
                ],
                "execution_cache_write_5m_input_tokens": pricing[
                    "cache_write_5m_input_tokens"
                ],
                "execution_cache_write_1h_input_tokens": pricing[
                    "cache_write_1h_input_tokens"
                ],
                "execution_standard_uncached_input_tokens": pricing[
                    "standard_uncached_input_tokens"
                ],
                "execution_standard_cached_input_tokens": pricing[
                    "standard_cached_input_tokens"
                ],
                "execution_standard_output_tokens": pricing["standard_output_tokens"],
                "execution_long_uncached_input_tokens": pricing[
                    "long_uncached_input_tokens"
                ],
                "execution_long_cached_input_tokens": pricing[
                    "long_cached_input_tokens"
                ],
                "execution_long_output_tokens": pricing["long_output_tokens"],
                "execution_output_tokens": outcome["output_tokens"],
                "execution_agent_seconds": outcome.get("agent_seconds"),
                "request_usage_count": pricing["request_count"],
                "cache_write_input_tokens_exact": pricing[
                    "cache_write_input_tokens_exact"
                ],
                "api_equivalent_price_exact": pricing["exact"],
                "shadow_price_schedule": pricing["schedule"],
                "claim_improved": outcome.get("claim_improved"),
                "claim_confidence": outcome.get("claim_confidence"),
                "claim_evidence": outcome.get("claim_evidence"),
                "claim_parsed": outcome.get("claim_parsed", False),
                "self_report": outcome.get("self_report"),
                "judge_verdict": outcome.get("judge_verdict"),
                "input_tokens": round(outcome["input_tokens"] * cost_share, 6),
                "uncached_input_tokens": round(
                    (
                        pricing["standard_uncached_input_tokens"]
                        + pricing["long_uncached_input_tokens"]
                    )
                    * cost_share,
                    6,
                ),
                "cached_input_tokens": round(
                    (
                        pricing["standard_cached_input_tokens"]
                        + pricing["long_cached_input_tokens"]
                    )
                    * cost_share,
                    6,
                ),
                "cache_write_input_tokens": round(
                    pricing["cache_write_input_tokens"] * cost_share, 6
                ),
                "cache_write_5m_input_tokens": round(
                    pricing["cache_write_5m_input_tokens"] * cost_share, 6
                ),
                "cache_write_1h_input_tokens": round(
                    pricing["cache_write_1h_input_tokens"] * cost_share, 6
                ),
                "output_tokens": round(outcome["output_tokens"] * cost_share, 6),
                "agent_seconds": round(
                    (outcome.get("agent_seconds") or 0.0) * cost_share, 6
                ),
                "judge_seconds": 0.0,
                "billing_mode": manifest["billing_mode"],
                "execution_mode": manifest["execution_mode"],
                "apparatus_test": bool(manifest.get("apparatus_test", False)),
                "api_equivalent_usd": round(shadow_usd, 6),
                "api_equivalent_usd_lower_bound": round(shadow_lower_usd, 6),
                "incremental_billed_usd": round(incremental_billed_usd, 6),
                "quota_wait_events": outcome.get("quota_wait_events", 0),
                "quota_wait_seconds": outcome.get("quota_wait_seconds", 0.0),
                "credential_refresh_persisted": outcome.get(
                    "credential_refresh_persisted", False
                ),
                "credential_leak_scan_passed": outcome.get(
                    "credential_leak_scan_passed", False
                ),
                "wall_clock_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            try:
                usage_record["candidate_archive_manifest_sha256"] = (
                    archive_candidate(candidate_dir, archive_root)
                    if archive_root is not None
                    else None
                )
            except Exception as exc:
                raise TrajectoryRunError(
                    exc,
                    {**usage_record, "artifact_archive_failed": True, "shared_cycle_one_failure": cycle == 1},
                ) from exc
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
                        "oracle_metrics_hoa": oracle_a_result.get("metrics", {}),
                        "oracle_metrics_hob": oracle_b_result.get("metrics", {}),
                        "reward_hack_signals": sorted(
                            set(oracle_a_result.get("metrics", {}).get("reward_hack_signals", []))
                            | set(oracle_b_result.get("metrics", {}).get("reward_hack_signals", []))
                        ),
                        "canary_leak": bool(
                            oracle_a_result.get("metrics", {}).get("canary_leak")
                            or oracle_b_result.get("metrics", {}).get("canary_leak")
                        ),
                        "oracle_valid": bool(
                            oracle_a_result.get("valid") and oracle_b_result.get("valid")
                        ),
                        "execution_oracle_seconds": round(
                            oracle_a_seconds + oracle_b_seconds, 6
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
    if manifest.get("candidate_sandbox_image"):
        os.environ["LOOP_SANDBOX_IMAGE"] = manifest["candidate_sandbox_image"]
    cycles = int(manifest["cycles"])
    everything = trajectories(manifest)
    common_groups_total = {
        (key.task, key.agent, key.model, key.seed) for key in everything
    }
    logical_cycle_rows = len(everything) * cycles
    unique_agent_executions = len(common_groups_total) + len(everything) * max(0, cycles - 1)
    digest = manifest_digest(manifest)
    done = completed_common_group_trajectories(args.log, manifest, cycles, digest)
    todo = [key for key in everything if key.token() not in done]
    incomplete_markers = incomplete_common_attempt_markers(
        args.log, manifest, cycles, digest
    )

    estimate_per_trajectory = float(manifest["estimated_api_equivalent_usd_per_trajectory"])
    total_shadow_estimate = estimate_per_trajectory * len(todo)
    billing_mode = manifest["billing_mode"]
    total_billed_estimate = total_shadow_estimate if billing_mode == "api" else 0.0
    ceiling = float(manifest["cost_ceiling_usd"]) if billing_mode == "api" else None
    prior_shadow, prior_billed = logged_costs(args.log, digest)
    within_ceiling = ceiling is None or prior_billed + total_billed_estimate <= ceiling
    concurrency = int(manifest["max_concurrent_agents"])
    lane_limits = worker_lane_limits(manifest)
    archive_root = None
    if manifest.get("artifact_archive_dir"):
        configured_archive = Path(manifest["artifact_archive_dir"])
        archive_root = (
            configured_archive
            if configured_archive.is_absolute()
            else (args.manifest.parent / configured_archive).resolve()
        )

    print(
        json.dumps(
            {
                "run_id": args.run_id,
                "manifest_digest": digest,
                "trajectories_total": len(everything),
                "trajectories_complete": len(done),
                "trajectories_remaining": len(todo),
                "incomplete_common_group_rows_pending_abandonment": len(
                    incomplete_markers
                ),
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
                "worker_lane_limits": lane_limits,
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
    for marker in incomplete_markers:
        log.write(marker)
    budget = Budget(billing_mode, ceiling, prior_shadow, prior_billed)
    agents_by_name = {entry["name"]: entry for entry in manifest["agents"]}
    stopped_for_budget = []
    abandoned_attempts = 0
    common_consumer_counts: dict[tuple[str, str, str, int], int] = {}
    common_members: dict[tuple[str, str, str, int], list[TrajectoryKey]] = {}
    for key in todo:
        group = common_group(key)
        common_consumer_counts[group] = common_consumer_counts.get(group, 0) + 1
        common_members.setdefault(group, []).append(key)
    attempt_ids = {
        group: f"{args.run_id}:{uuid.uuid4().hex}" for group in common_members
    }
    common_first_cycle = CommonFirstCycleCache()
    failed_groups: set[tuple[str, str, str, int]] = set()
    individually_abandoned: set[str] = set()

    try:
        with ExitStack() as stack:
            if "shared" in lane_limits:
                shared_pool = stack.enter_context(
                    ThreadPoolExecutor(max_workers=lane_limits["shared"])
                )
                pools = {"shared": shared_pool}
            else:
                pools = {
                    lane: stack.enter_context(ThreadPoolExecutor(max_workers=limit))
                    for lane, limit in lane_limits.items()
                }
            futures = {}
            if not budget.reserve(total_shadow_estimate):
                stopped_for_budget.extend(key.token() for key in todo)
            for key in ([] if stopped_for_budget else scheduled_trajectories(todo, manifest)):
                group = common_group(key)
                attempt_id = attempt_ids[group]
                entry = agents_by_name[key.agent]
                adapter = entry.get("adapter", entry.get("name"))
                pool = pools.get("shared") or pools.get(adapter) or pools.get("other")
                if pool is None:
                    raise RuntimeError(f"no worker lane is available for adapter {adapter!r}")
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
                        archive_root,
                    )
                ] = (key, group, attempt_id, estimate_per_trajectory)
            for future in as_completed(futures):
                key, group, attempt_id, reservation = futures[future]
                try:
                    result = future.result()
                except TrajectoryRunError as exc:
                    abandoned_attempts += 1
                    failed_groups.add(group)
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
                    failed_groups.add(group)
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
            for group in failed_groups:
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
                            "error": "common-cycle block peer failed; all branches must rerun",
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
