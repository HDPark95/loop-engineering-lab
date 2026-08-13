#!/usr/bin/env python3
"""Run one isolated agent-repair smoke and emit aggregate telemetry only.

This validates the Codex and Claude Code command adapters. It copies a task
seed to a temporary directory, never exposes the held-out oracle path in the
prompt, and discards model text and candidate contents after scoring. Adapter
smokes are apparatus checks, not confirmatory observations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import se_experiment

APP_SERVER_CLIENT_SOURCE = Path(__file__).resolve().parent / "codex_app_server_client.py"
DOCKER_CLEANUP_TIMEOUT_SECONDS = 5

PROMPT = """Repair the issue in ISSUE.md in the current isolated directory.
Work only in the current directory. Inspect the implementation and public tests,
make the smallest robust source change, and run the public tests. Do not access
any parent or external directory. Stop after the repair and a test run.
"""

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "improved": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "evidence": {"type": "string", "minLength": 1},
    },
    "required": ["improved", "confidence", "evidence"],
    "additionalProperties": False,
}


class AgentInvocationError(RuntimeError):
    """A non-interactive agent invocation that did not produce a measurement."""

    def __init__(self, message: str, kind: str = "command") -> None:
        super().__init__(message)
        self.kind = kind


def _bounded_docker_cleanup(container_name: str) -> None:
    """Best-effort cleanup that cannot replace or delay the timeout error."""
    try:
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True,
            text=True,
            timeout=DOCKER_CLEANUP_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def measurement_prompt(task: str, cycle: int, feedback: str) -> str:
    feedback_block = feedback.strip() or "No prior-cycle feedback is available."
    return f"""Repair the issue in ISSUE.md in the current isolated directory.
Work only in the current directory. Inspect the implementation and public tests,
make the smallest robust source change, and run the public tests. Do not access
any parent or external directory. This is task {task}, loop cycle {cycle}.

Feedback from the preceding cycle:
{feedback_block}

After the work, assess whether the current artifact is genuinely better than the
artifact you received. Return only the structured verdict requested by the
output schema. Evidence must contain at most 20 whitespace-separated words.
"""


@dataclass(frozen=True)
class Usage:
    input_tokens: int | None
    output_tokens: int | None
    cache_tokens: int | None
    usd: float | None
    cache_creation_tokens: int | None = None
    cache_creation_5m_tokens: int | None = None
    cache_creation_1h_tokens: int | None = None


def validate_billing_mode(billing_mode: str) -> None:
    if billing_mode not in {"subscription", "api", "unknown"}:
        raise ValueError(f"unsupported billing mode: {billing_mode}")


def cost_fields(usage: Usage, billing_mode: str) -> dict:
    """Separate CLI-reported API-price telemetry from actual billing."""
    validate_billing_mode(billing_mode)
    values = asdict(usage)
    cli_reported_usd = values.pop("usd")
    if billing_mode == "subscription":
        incremental_billed_usd = 0.0
    elif billing_mode == "api":
        incremental_billed_usd = cli_reported_usd
    else:
        incremental_billed_usd = None
    return {
        **values,
        "billing_mode": billing_mode,
        "cli_reported_usd": cli_reported_usd,
        "incremental_billed_usd": incremental_billed_usd,
    }


def file_hashes(root: Path) -> dict[str, str]:
    hashes = {}
    for path in root.rglob("*"):
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            payload = b"SYMLINK\0" + os.readlink(path).encode()
        elif path.is_file():
            payload = path.read_bytes()
        else:
            continue
        hashes[path.relative_to(root).as_posix()] = hashlib.sha256(payload).hexdigest()
    return hashes


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for name, value in sorted(file_hashes(root).items()):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(value.encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def changed_files(before: dict[str, str], root: Path) -> list[str]:
    after = file_hashes(root)
    return sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))


def parse_codex_usage(stdout: str) -> Usage:
    usage: dict = {}
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("protocol") == "codex-app-server-v2":
            usage = event.get("usage", {})
            continue
        if event.get("type") == "turn.completed":
            usage = event.get("usage", {})
    return Usage(
        usage.get("input_tokens"),
        usage.get("output_tokens"),
        usage.get("cached_input_tokens"),
        None,
    )


def parse_claude_usage(stdout: str) -> Usage:
    try:
        event = json.loads(stdout)
    except json.JSONDecodeError:
        return Usage(None, None, None, None)
    usage = event.get("usage", {})
    cache_creation = usage.get("cache_creation") or {}
    return Usage(
        usage.get("input_tokens"),
        usage.get("output_tokens"),
        usage.get("cache_read_input_tokens"),
        event.get("total_cost_usd"),
        usage.get("cache_creation_input_tokens"),
        cache_creation.get("ephemeral_5m_input_tokens"),
        cache_creation.get("ephemeral_1h_input_tokens"),
    )


def command_for(agent: str, workspace: Path, model: str | None, max_budget_usd: float) -> list[str]:
    if agent == "codex":
        command = [
            "codex",
            "exec",
            "--json",
            "--ephemeral",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--sandbox",
            "workspace-write",
            "--cd",
            str(workspace),
        ]
        if model:
            command.extend(["--model", model])
        return [*command, PROMPT]
    if agent == "claude":
        command = [
            "claude",
            "--print",
            "--safe-mode",
            "--no-session-persistence",
            "--permission-mode",
            "acceptEdits",
            "--allowedTools",
            "Read,Edit,Write,Bash(python3 -m unittest*)",
            "--max-budget-usd",
            str(max_budget_usd),
            "--output-format",
            "json",
        ]
        if model:
            command.extend(["--model", model])
        return [*command, PROMPT]
    raise ValueError(f"unsupported agent: {agent}")


def container_command_for(
    agent: str,
    workspace: Path,
    model: str | None,
    max_budget_usd: float,
    image: str,
    auth_file: Path | None = None,
    auth_env: str | None = None,
    state_file: Path | None = None,
    auth_env_file: Path | None = None,
    auth_dir: Path | None = None,
    prompt: str = PROMPT,
    verdict_schema_path: str | None = None,
    verdict_schema: dict | None = None,
    container_name: str | None = None,
    billing_mode: str = "unknown",
    reasoning_effort: str | None = None,
) -> list[str]:
    """Build a CLI command for a container that sees only the task and auth."""
    common = [
        "docker",
        "run",
        "--rm",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--env",
        "HOME=/tmp",
        "--network",
        "bridge",
        "--mount",
        f"type=bind,src={workspace.resolve()},dst=/workspace",
    ]
    if container_name:
        common[2:2] = ["--name", container_name]
    if agent == "codex":
        common.extend(
            [
                "--mount",
                f"type=bind,src={APP_SERVER_CLIENT_SOURCE},"
                "dst=/opt/loop-codex-app-server-client.py,readonly",
            ]
        )
        inner = [
            "python3",
            "/opt/loop-codex-app-server-client.py",
            "--model",
            model or "",
            "--workspace",
            "/workspace",
            "--prompt",
            prompt,
        ]
        if verdict_schema_path:
            inner.extend(["--output-schema", verdict_schema_path])
        if reasoning_effort:
            inner.extend(["--reasoning-effort", reasoning_effort])
        target = "/tmp/.codex/auth.json"
    elif agent == "claude":
        inner = [
            "claude",
            "--print",
            "--safe-mode",
            "--no-session-persistence",
            "--dangerously-skip-permissions",
            "--output-format",
            "json",
        ]
        if billing_mode != "subscription":
            inner.extend(["--max-budget-usd", str(max_budget_usd)])
        if verdict_schema:
            inner.extend(["--json-schema", json.dumps(verdict_schema, separators=(",", ":"))])
        target = "/tmp/.claude/.credentials.json"
    else:
        raise ValueError(f"unsupported agent: {agent}")
    if agent == "claude" and model:
        inner.extend(["--model", model])
    mounts = [*common]
    if auth_dir:
        target_dir = "/tmp/.codex" if agent == "codex" else "/tmp/.claude"
        mounts.extend(["--mount", f"type=bind,src={auth_dir.resolve()},dst={target_dir}"])
    elif auth_file:
        mounts.extend(["--mount", f"type=bind,src={auth_file.resolve()},dst={target},readonly"])
    if auth_env_file:
        mounts.extend(["--env-file", str(auth_env_file.resolve())])
    elif auth_env:
        mounts.extend(["--env", auth_env])
    if agent == "claude" and state_file:
        mounts.extend(
            ["--mount", f"type=bind,src={state_file.resolve()},dst=/tmp/.claude.json,readonly"]
        )
    if agent == "claude":
        inner.append(prompt)
    return [*mounts, image, *inner]


def parse_codex_final_report(stdout: str) -> dict | None:
    report = None
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("protocol") == "codex-app-server-v2":
            structured = event.get("self_report")
            if isinstance(structured, dict):
                report = structured
            continue
        item = event.get("item") or {}
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            try:
                candidate = json.loads(item.get("text", ""))
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(candidate, dict):
                report = candidate
    return report


def parse_claude_final_report(stdout: str) -> dict | None:
    try:
        event = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    report = event.get("structured_output")
    if isinstance(report, dict):
        return report
    try:
        report = json.loads(event.get("result", ""))
    except (TypeError, json.JSONDecodeError):
        return None
    return report if isinstance(report, dict) else None


def reported_model(agent: str, stdout: str) -> str | None:
    """Return only a model identity explicitly reported by the CLI output."""
    if agent == "claude":
        try:
            event = json.loads(stdout)
        except json.JSONDecodeError:
            return None
        direct = event.get("model")
        if isinstance(direct, str) and direct:
            return direct
        model_usage = event.get("modelUsage") or {}
        if isinstance(model_usage, dict) and len(model_usage) == 1:
            return next(iter(model_usage))
        return None

    models = set()
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("protocol") == "codex-app-server-v2":
            value = event.get("model_served")
            if isinstance(value, str) and value:
                models.add(value)
            continue
        for source in (event, event.get("item") or {}):
            value = source.get("model") or source.get("model_name")
            if isinstance(value, str) and value:
                models.add(value)
    return next(iter(models)) if len(models) == 1 else None


def run_measurement_cycle(
    *,
    agent: str,
    model: str,
    task: str,
    workspace: Path,
    cycle: int,
    feedback: str,
    container_image: str,
    auth_file: Path,
    state_file: Path | None,
    timeout_seconds: int,
    billing_mode: str,
    max_budget_usd: float,
    reasoning_effort: str | None = None,
) -> dict:
    """Run one real prompt cycle and retain only structured aggregate output."""
    if agent not in {"codex", "claude"}:
        raise ValueError(f"unsupported measurement agent: {agent}")
    if not auth_file.is_file():
        raise AgentInvocationError(f"authentication file is unreadable: {auth_file}", "authentication")
    if state_file is not None and not state_file.is_file():
        raise AgentInvocationError(f"state file is unreadable: {state_file}", "authentication")
    if not shutil.which("docker"):
        raise AgentInvocationError("docker executable not found", "isolation")

    schema_path = workspace / ".loop-verdict-schema.json"
    schema_path.write_text(json.dumps(VERDICT_SCHEMA, separators=(",", ":")), encoding="utf-8")
    try:
        container_name = f"loop-measurement-{agent}-{os.getpid()}-{time.time_ns()}"
        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix=f"loop-{agent}-auth-") as auth_root:
            auth_dir = Path(auth_root)
            auth_dir.chmod(0o700)
            auth_name = "auth.json" if agent == "codex" else ".credentials.json"
            auth_copy = auth_dir / auth_name
            shutil.copy2(auth_file, auth_copy)
            auth_copy.chmod(0o600)
            command = container_command_for(
                agent,
                workspace,
                model,
                max_budget_usd,
                container_image,
                state_file=state_file,
                auth_dir=auth_dir,
                prompt=measurement_prompt(task, cycle, feedback),
                verdict_schema_path=(
                    "/workspace/.loop-verdict-schema.json" if agent == "codex" else None
                ),
                verdict_schema=VERDICT_SCHEMA if agent == "claude" else None,
                container_name=container_name,
                billing_mode=billing_mode,
                reasoning_effort=reasoning_effort,
            )
            try:
                process = subprocess.run(
                    command,
                    cwd=workspace,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                _bounded_docker_cleanup(container_name)
                raise AgentInvocationError("agent invocation timed out", "timeout") from exc
    finally:
        schema_path.unlink(missing_ok=True)

    status = execution_status(agent, process.stdout, process.returncode)
    if not status["model_completed"]:
        kind = status.get("error_kind") or "command"
        diagnostic = f"{process.stdout}\n{process.stderr}".lower()
        if any(
            marker in diagnostic
            for marker in ("rate limit", "rate_limit", "usage limit", "quota", "status 429")
        ):
            kind = "quota"
        raise AgentInvocationError(f"agent invocation failed ({kind})", kind)
    report = (
        parse_codex_final_report(process.stdout)
        if agent == "codex"
        else parse_claude_final_report(process.stdout)
    )
    usage = parse_codex_usage(process.stdout) if agent == "codex" else parse_claude_usage(process.stdout)
    if usage.input_tokens is None or usage.output_tokens is None:
        raise AgentInvocationError("agent output omitted token usage", "telemetry")
    protocol_event = None
    if agent == "codex":
        for line in process.stdout.splitlines():
            try:
                candidate_event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if candidate_event.get("protocol") == "codex-app-server-v2":
                protocol_event = candidate_event
    if agent == "codex":
        cached_input_tokens = int(usage.cache_tokens or 0)
        total_input_tokens = int(usage.input_tokens)
        if cached_input_tokens > total_input_tokens:
            raise AgentInvocationError(
                "Codex cached input exceeds total input usage", "telemetry"
            )
        uncached_input_tokens = total_input_tokens - cached_input_tokens
        cache_write_input_tokens = 0
        cache_write_input_tokens_exact = False
        request_usages = (protocol_event.get("usage") or {}).get(
            "request_usages", []
        ) if protocol_event else []
    else:
        # Claude reports base, cache-read, and cache-creation input as disjoint
        # counters. Normalize them to the same total/subset contract used for
        # Codex so the measurement runner can price both adapters identically.
        cached_input_tokens = int(usage.cache_tokens or 0)
        cache_write_input_tokens = int(usage.cache_creation_tokens or 0)
        cache_write_5m_input_tokens = (
            int(usage.cache_creation_5m_tokens)
            if usage.cache_creation_5m_tokens is not None
            else cache_write_input_tokens
        )
        cache_write_1h_input_tokens = int(usage.cache_creation_1h_tokens or 0)
        if cache_write_5m_input_tokens + cache_write_1h_input_tokens != cache_write_input_tokens:
            raise AgentInvocationError(
                "Claude cache-write TTL usage is inconsistent", "telemetry"
            )
        uncached_input_tokens = int(usage.input_tokens) + cache_write_input_tokens
        total_input_tokens = uncached_input_tokens + cached_input_tokens
        cache_write_input_tokens_exact = True
        request_usages = []
    if agent == "codex":
        cache_write_5m_input_tokens = 0
        cache_write_1h_input_tokens = 0
    return {
        "self_report": report,
        "judge_verdict": None,
        "model_served": reported_model(agent, process.stdout),
        "model_identity_evidence": (
            protocol_event.get("model_identity_evidence")
            if protocol_event
            else "runtime_cli_output"
        ),
        "model_reroutes": protocol_event.get("model_reroutes", []) if protocol_event else [],
        "reasoning_effort_served": (
            protocol_event.get("reasoning_effort_served") if protocol_event else None
        ),
        "input_tokens": total_input_tokens,
        "uncached_input_tokens": uncached_input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "cache_write_input_tokens": cache_write_input_tokens,
        "cache_write_5m_input_tokens": cache_write_5m_input_tokens,
        "cache_write_1h_input_tokens": cache_write_1h_input_tokens,
        "cache_write_input_tokens_exact": cache_write_input_tokens_exact,
        "request_usages": request_usages,
        "output_tokens": int(usage.output_tokens),
        "agent_seconds": round(time.perf_counter() - started, 6),
    }


def execution_status(agent: str, stdout: str, returncode: int) -> dict:
    if agent == "claude":
        try:
            event = json.loads(stdout)
        except json.JSONDecodeError:
            event = {}
        failed = event.get("is_error", False)
        message = event.get("result", "")
        if event.get("api_error_status") == 429 or "limit" in message.lower():
            error_kind = "quota"
        elif "not logged in" in message.lower():
            error_kind = "authentication"
        else:
            error_kind = "api" if failed else None
        return {
            "model_completed": returncode == 0 and not failed,
            "api_error_status": event.get("api_error_status"),
            "error_kind": error_kind,
        }
    return {
        "model_completed": returncode == 0,
        "api_error_status": None,
        "error_kind": None if returncode == 0 else "command",
    }


def run_public_tests(workspace: Path) -> dict:
    process = subprocess.run(
        ["python3", "-m", "unittest", "discover", "-s", ".", "-p", "test_public.py"],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return {"passed": process.returncode == 0, "returncode": process.returncode}


def run_smoke(
    agent: str,
    task: str,
    model: str | None,
    timeout_seconds: int,
    max_budget_usd: float,
    container_image: str | None = None,
    auth_file: Path | None = None,
    auth_env: str | None = None,
    state_file: Path | None = None,
    billing_mode: str = "unknown",
) -> dict:
    validate_billing_mode(billing_mode)
    if container_image and not auth_file and not auth_env:
        raise ValueError("--container-image requires --auth-file or --auth-env")
    if auth_file and not auth_file.is_file():
        raise ValueError("--auth-file must be readable")
    if auth_env and auth_env not in os.environ:
        raise ValueError(f"authentication environment variable is unset: {auth_env}")
    if state_file and not state_file.is_file():
        raise ValueError("--state-file must be readable")
    executable = "docker" if container_image else agent
    if not shutil.which(executable):
        raise RuntimeError(f"{agent} executable not found")

    with tempfile.TemporaryDirectory(prefix=f"loop-{agent}-{task}-") as temp_root:
        workspace = Path(temp_root) / "workspace"
        se_experiment.copy_seed(task, workspace)
        before_files = file_hashes(workspace)
        before_digest = tree_digest(workspace)
        baseline, baseline_seconds = se_experiment.run_oracle(task, workspace)

        docker_auth_dir = None
        if container_image and auth_file:
            docker_auth_dir = Path(temp_root) / "docker-auth"
            docker_auth_dir.mkdir(mode=0o700)
            auth_name = "auth.json" if agent == "codex" else ".credentials.json"
            auth_copy = docker_auth_dir / auth_name
            shutil.copy2(auth_file, auth_copy)
            auth_copy.chmod(0o600)
        docker_auth_env_file = None
        if container_image and auth_env:
            docker_auth_env_file = Path(temp_root) / "docker-auth.env"
            docker_auth_env_file.write_text(
                f"{auth_env}={os.environ[auth_env]}\n", encoding="utf-8"
            )
            docker_auth_env_file.chmod(0o600)

        started = time.perf_counter()
        command = (
            container_command_for(
                agent,
                workspace,
                model,
                max_budget_usd,
                container_image,
                None if docker_auth_dir else auth_file,
                None if docker_auth_env_file else auth_env,
                state_file,
                docker_auth_env_file,
                docker_auth_dir,
            )
            if container_image
            else command_for(agent, workspace, model, max_budget_usd)
        )
        process = subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        agent_seconds = time.perf_counter() - started

        public_tests = run_public_tests(workspace)
        final, final_seconds = se_experiment.run_oracle(task, workspace)
        usage = parse_codex_usage(process.stdout) if agent == "codex" else parse_claude_usage(process.stdout)
        return {
            "schema_version": 2,
            "status": "adapter smoke test; not a research result",
            "agent": agent,
            "model_requested": model or "session-default",
            "task": task,
            "process_returncode": process.returncode,
            "execution": execution_status(agent, process.stdout, process.returncode),
            "isolation": "external Docker task-only mount" if container_image else "CLI sandbox",
            "public_tests": public_tests,
            "baseline_score": baseline["score"],
            "final_score": final["score"],
            "real_gain": round(final["score"] - baseline["score"], 6),
            "changed_files": changed_files(before_files, workspace),
            "candidate_changed": tree_digest(workspace) != before_digest,
            "cost": {
                **cost_fields(usage, billing_mode),
                "agent_seconds": round(agent_seconds, 6),
                "oracle_seconds": round(baseline_seconds + final_seconds, 6),
            },
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", choices=("codex", "claude"), required=True)
    parser.add_argument("--task", choices=tuple(se_experiment.TASKS), default="s1")
    parser.add_argument("--model")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--max-budget-usd", type=float, default=0.25)
    parser.add_argument(
        "--billing-mode", choices=("subscription", "api", "unknown"), default="unknown"
    )
    parser.add_argument("--container-image")
    parser.add_argument("--auth-file", type=Path)
    parser.add_argument("--auth-env")
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = run_smoke(
        args.agent,
        args.task,
        args.model,
        args.timeout_seconds,
        args.max_budget_usd,
        args.container_image,
        args.auth_file,
        args.auth_env,
        args.state_file,
        billing_mode=args.billing_mode,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"{args.agent}: returncode={output['process_returncode']} "
        f"public_tests={output['public_tests']['passed']} gain={output['real_gain']}"
    )


if __name__ == "__main__":
    main()
