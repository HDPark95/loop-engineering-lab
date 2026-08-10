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

**A budget ceiling checked before each trajectory, not after.** A grid that
overruns its ceiling halfway leaves a partial design, which is worse than not
starting. The runner stops cleanly on the boundary and says how much of the grid
is missing.

Model identity is recorded twice: what the manifest asked for and what the
runtime reported serving. An alias resolves to different weights on different
days, and a measurement taken across days under one alias is a mixture of
models. A mismatch is recorded on the cycle rather than corrected.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import se_experiment

ROOT = Path(__file__).resolve().parent
SCHEMA_VERSION = 1

# Founder-approved lane rule: at most three agent processes at once. Interactive
# work and the automation fleet share one account, so an unbounded fan-out takes
# both down with it. This is a hard cap, not a default.
MAX_CONCURRENT_AGENTS = 3


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


def load_manifest(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    missing = [
        field
        for field in ("tasks", "agents", "cells", "seeds", "cycles", "cost_ceiling_usd", "preregistration_commit")
        if field not in manifest
    ]
    if missing:
        raise SystemExit(f"manifest is missing required fields: {', '.join(missing)}")

    for agent in manifest["agents"]:
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
            raise SystemExit(f"agent {agent.get('name')!r} has no price entry")

    if not manifest["preregistration_commit"]:
        raise SystemExit(
            "preregistration_commit is empty. The registration must be frozen to a "
            "commit before confirmatory measurement starts."
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


def completed_trajectories(log_path: Path, cycles: int) -> set[str]:
    """Tokens whose cycles are all present and not marked abandoned.

    A trajectory that stopped partway is not resumed from its last cycle. Its
    records stay in the log, marked, and it is rerun from the start.
    """
    if not log_path.exists():
        return set()
    seen: dict[str, set[int]] = {}
    abandoned: set[str] = set()
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
            if record.get("abandoned"):
                abandoned.add(token)
                continue
            seen.setdefault(token, set()).add(record.get("cycle"))
    return {
        token
        for token, cycle_numbers in seen.items()
        if token not in abandoned and len(cycle_numbers) >= cycles
    }


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
    """Cumulative spend with a ceiling checked before each trajectory starts."""

    def __init__(self, ceiling_usd: float) -> None:
        self.ceiling = ceiling_usd
        self._spent = 0.0
        self._lock = threading.Lock()

    def add(self, usd: float) -> None:
        with self._lock:
            self._spent += usd

    @property
    def spent(self) -> float:
        with self._lock:
            return self._spent

    def can_start(self, estimate_usd: float) -> bool:
        with self._lock:
            return self._spent + estimate_usd <= self.ceiling


def scripted_driver(model: str, task: str, workspace: Path, cycle: int, seed: int, manifest: dict) -> dict:
    """A deterministic stand-in agent, for exercising the runner without an API.

    It is not a research instrument and must never appear in a manifest used for
    a confirmatory run. `load_manifest` rejects alias model identifiers, and this
    driver's model string is one, so a manifest that reaches it cannot also pass
    the confirmatory checks.

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
        "self_report": {"improved": candidate.claim_improved, "source": "scripted"},
        "judge_verdict": None,
        "model_served": model,
        "candidate_digest": digest_of(workspace),
        "input_tokens": candidate.input_tokens,
        "output_tokens": candidate.output_tokens,
        "agent_seconds": round(time.perf_counter() - started, 6),
    }


def real_agent_driver(model: str, task: str, workspace: Path, cycle: int, seed: int, manifest: dict) -> dict:
    raise NotImplementedError(
        "The coding-agent binding is not wired yet. It needs a credentialed run to "
        "verify, and shipping it untested is the failure this study is about. "
        "What remains: drive the agent over `workspace` through the container path "
        "in agent_adapters, parse its structured self-report, and return the same "
        "keys scripted_driver returns."
    )


DRIVERS = {"scripted": scripted_driver}


def driver_for(agent: str):
    return DRIVERS.get(agent, real_agent_driver)


def digest_of(root: Path) -> str:
    hasher = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        hasher.update(path.relative_to(root).as_posix().encode())
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

    with tempfile.TemporaryDirectory(prefix="loop-eng-traj-") as workspace_root:
        deployed = Path(workspace_root) / "deployed"
        se_experiment.copy_seed(key.task, deployed)
        baseline_result, _ = se_experiment.run_oracle(key.task, deployed)
        baseline = baseline_result["score"]
        deployed_score = baseline

        for cycle in range(1, cycles + 1):
            candidate_dir = Path(workspace_root) / f"candidate-{cycle}"
            shutil.copytree(deployed, candidate_dir)
            outcome = driver(
                model=key.model,
                task=key.task,
                workspace=candidate_dir,
                cycle=cycle,
                seed=key.seed,
                manifest=manifest,
            )
            oracle_result, oracle_seconds = se_experiment.run_oracle(key.task, candidate_dir)
            oracle = oracle_result["score"]
            delta = oracle - deployed_score
            accepted = se_experiment.gate_decision(cell, outcome["claim_improved"], delta)
            if accepted:
                # Promotion is what "deployed" means. A rejected candidate is
                # discarded and the next cycle starts from the last accepted state.
                shutil.rmtree(deployed)
                shutil.copytree(candidate_dir, deployed)
                deployed_score = oracle
            usd = price_of(agent_entry, outcome["input_tokens"], outcome["output_tokens"])
            trajectory_cost += usd
            budget.add(usd)

            log.write(
                {
                    "schema_version": SCHEMA_VERSION,
                    "run_id": run_id,
                    "manifest_digest": manifest_digest(manifest),
                    "preregistration_commit": manifest["preregistration_commit"],
                    "trajectory": key.token(),
                    **key.as_dict(),
                    "cell_gate_grounded": cell.gate_grounded,
                    "cell_feedback": cell.feedback,
                    "cycle": cycle,
                    "model_requested": key.model,
                    "model_served": outcome.get("model_served"),
                    "model_identity_matches": outcome.get("model_served") in (None, key.model),
                    "candidate_digest": outcome.get("candidate_digest"),
                    "claim_improved": outcome["claim_improved"],
                    "self_report": outcome.get("self_report"),
                    "judge_verdict": outcome.get("judge_verdict"),
                    "oracle_score": oracle,
                    "oracle_delta": delta,
                    "accepted": accepted,
                    "deployed_score": deployed_score,
                    "baseline_score": baseline,
                    "canary_leak": bool(oracle_result.get("metrics", {}).get("canary_leak")),
                    "oracle_valid": oracle_result.get("valid"),
                    "input_tokens": outcome["input_tokens"],
                    "output_tokens": outcome["output_tokens"],
                    "agent_seconds": outcome.get("agent_seconds"),
                    "oracle_seconds": round(oracle_seconds, 6),
                    "usd": round(usd, 6),
                    "wall_clock_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
            )

    return {
        "trajectory": key.token(),
        "cost_usd": round(trajectory_cost, 6),
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
    cycles = int(manifest["cycles"])
    everything = trajectories(manifest)
    done = completed_trajectories(args.log, cycles)
    todo = [key for key in everything if key.token() not in done]

    estimate_per_trajectory = float(manifest.get("estimated_usd_per_trajectory", 0.0))
    total_estimate = estimate_per_trajectory * len(todo)
    ceiling = float(manifest["cost_ceiling_usd"])

    print(
        json.dumps(
            {
                "run_id": args.run_id,
                "manifest_digest": manifest_digest(manifest),
                "trajectories_total": len(everything),
                "trajectories_complete": len(done),
                "trajectories_remaining": len(todo),
                "cycles_per_trajectory": cycles,
                "estimated_usd_remaining": round(total_estimate, 2),
                "cost_ceiling_usd": ceiling,
                "within_ceiling": total_estimate <= ceiling,
                "max_concurrent_agents": MAX_CONCURRENT_AGENTS,
            },
            indent=2,
            sort_keys=True,
        )
    )
    if args.plan_only:
        return 0
    if total_estimate > ceiling:
        print(
            "refusing to start: the remaining grid is estimated above the ceiling. "
            "Raise the ceiling deliberately or cut the grid; do not discover this "
            "halfway through.",
            file=sys.stderr,
        )
        return 2

    log = CycleLog(args.log)
    budget = Budget(ceiling)
    agents_by_name = {entry["name"]: entry for entry in manifest["agents"]}
    stopped_for_budget = []

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_AGENTS) as pool:
        futures = {}
        for key in todo:
            if not budget.can_start(estimate_per_trajectory):
                stopped_for_budget.append(key.token())
                continue
            futures[
                pool.submit(
                    run_trajectory,
                    key,
                    manifest,
                    agents_by_name[key.agent],
                    log,
                    budget,
                    args.run_id,
                )
            ] = key
        for future in as_completed(futures):
            key = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001 - one trajectory must not end the run
                log.write(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "run_id": args.run_id,
                        "trajectory": key.token(),
                        **key.as_dict(),
                        "abandoned": True,
                        "error": f"{type(exc).__name__}: {exc}",
                        "wall_clock_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    }
                )
                print(f"abandoned {key.token()}: {type(exc).__name__}", file=sys.stderr)
                continue
            print(json.dumps(result, sort_keys=True))

    summary = {
        "run_id": args.run_id,
        "spent_usd": round(budget.spent, 4),
        "cost_ceiling_usd": ceiling,
        "trajectories_not_started_for_budget": len(stopped_for_budget),
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
