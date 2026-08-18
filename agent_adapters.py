#!/usr/bin/env python3
"""Run one isolated agent-repair smoke and emit aggregate telemetry only.

This validates the Codex and Claude Code command adapters. It copies a task
seed to a temporary directory, never exposes the held-out oracle path in the
prompt, and discards model text and candidate contents after scoring. Adapter
smokes are apparatus checks, not confirmatory observations.
"""

from __future__ import annotations

import argparse
import base64
from contextlib import nullcontext
from datetime import datetime
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import se_experiment

APP_SERVER_CLIENT_SOURCE = Path(__file__).resolve().parent / "codex_app_server_client.py"
DOCKER_CLEANUP_TIMEOUT_SECONDS = 5
CLAUDE_CREDENTIAL_LOCK = threading.Lock()
CODEX_CREDENTIAL_LOCK = threading.Lock()
SECRET_KEY_MARKERS = ("token", "api_key", "apikey", "secret")
SANITIZED_CLAUDE_STATE = {
    "hasCompletedOnboarding": True,
    "projects": {
        "/workspace": {
            "hasTrustDialogAccepted": True,
            "hasCompletedProjectOnboarding": True,
        }
    },
}
SANITIZED_CLAUDE_SETTINGS = {"skipDangerousModePermissionPrompt": True}

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


def _read_private_json(path: Path) -> dict:
    """Read a runner-owned credential file without ever echoing its contents."""
    try:
        status = path.lstat()
    except OSError as exc:
        raise AgentInvocationError("authentication file is unreadable", "authentication") from exc
    if path.is_symlink() or not path.is_file():
        raise AgentInvocationError(
            "authentication state must be a regular file, not a symlink",
            "authentication",
        )
    if status.st_uid != os.getuid() or status.st_mode & 0o777 != 0o600:
        raise AgentInvocationError(
            "authentication state must be owned by the runner and mode 0600",
            "authentication",
        )
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AgentInvocationError("authentication state is not valid JSON", "authentication") from exc
    if not isinstance(document, dict):
        raise AgentInvocationError("authentication state must be a JSON object", "authentication")
    return document


def _claude_oauth(document: dict) -> dict:
    oauth = document.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        raise AgentInvocationError(
            "Claude authentication state has no claudeAiOauth record",
            "authentication",
        )
    for field in ("accessToken", "refreshToken", "subscriptionType", "rateLimitTier"):
        if not isinstance(oauth.get(field), str) or not oauth[field]:
            raise AgentInvocationError(
                f"Claude authentication state has invalid {field}",
                "authentication",
            )
    expires_at = oauth.get("expiresAt")
    if (
        not isinstance(expires_at, (int, float))
        or isinstance(expires_at, bool)
        or expires_at <= 0
    ):
        raise AgentInvocationError(
            "Claude authentication state has invalid expiresAt",
            "authentication",
        )
    scopes = oauth.get("scopes")
    if (
        not isinstance(scopes, list)
        or not scopes
        or any(not isinstance(scope, str) or not scope for scope in scopes)
    ):
        raise AgentInvocationError(
            "Claude authentication state has invalid scopes",
            "authentication",
        )
    return oauth


def _parse_utc_timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise AgentInvocationError(f"authentication state has invalid {field}", "authentication")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AgentInvocationError(
            f"authentication state has invalid {field}", "authentication"
        ) from exc
    if parsed.tzinfo is None:
        raise AgentInvocationError(f"authentication state has invalid {field}", "authentication")
    return parsed


def _jwt_expiry(token: object) -> int:
    if not isinstance(token, str) or token.count(".") != 2:
        raise AgentInvocationError("Codex access token is not a JWT", "authentication")
    payload = token.split(".")[1]
    try:
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        claims = json.loads(decoded)
    except (ValueError, json.JSONDecodeError) as exc:
        raise AgentInvocationError(
            "Codex access token has invalid JWT claims", "authentication"
        ) from exc
    expiry = claims.get("exp") if isinstance(claims, dict) else None
    if not isinstance(expiry, int) or isinstance(expiry, bool) or expiry <= 0:
        raise AgentInvocationError(
            "Codex access token has invalid expiry", "authentication"
        )
    return expiry


def _codex_chatgpt(document: dict) -> dict:
    if document.get("auth_mode") != "chatgpt":
        raise AgentInvocationError(
            "Codex authentication state is not ChatGPT subscription auth",
            "authentication",
        )
    tokens = document.get("tokens")
    if not isinstance(tokens, dict):
        raise AgentInvocationError("Codex authentication state has no tokens", "authentication")
    for field in ("access_token", "refresh_token", "id_token", "account_id"):
        if not isinstance(tokens.get(field), str) or not tokens[field]:
            raise AgentInvocationError(
                f"Codex authentication state has invalid {field}", "authentication"
            )
    _jwt_expiry(tokens["access_token"])
    _parse_utc_timestamp(document.get("last_refresh"), "last_refresh")
    return tokens


def credential_secret_values(document: dict) -> tuple[str, ...]:
    """Extract authentication secrets without returning account metadata."""
    secrets: set[str] = set()

    def visit(value, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                visit(child_value, str(child_key))
        elif isinstance(value, list):
            for child in value:
                visit(child, key)
        elif (
            isinstance(value, str)
            and len(value) >= 16
            and any(marker in key.lower() for marker in SECRET_KEY_MARKERS)
        ):
            secrets.add(value)

    visit(document)
    if not secrets:
        raise AgentInvocationError(
            "authentication state has no scannable secret values",
            "authentication",
        )
    return tuple(sorted(secrets))


def secret_leak_paths(root: Path, secrets: tuple[str, ...]) -> list[str]:
    """Return candidate-relative paths containing an exact credential value."""
    encoded = tuple(secret.encode("utf-8") for secret in secrets)
    maximum = max(len(secret) for secret in encoded)
    leaks = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            with path.open("rb") as handle:
                overlap = b""
                while chunk := handle.read(1024 * 1024):
                    payload = overlap + chunk
                    if any(secret in payload for secret in encoded):
                        leaks.append(path.relative_to(root).as_posix())
                        break
                    overlap = payload[-(maximum - 1):] if maximum > 1 else b""
        except OSError as exc:
            raise AgentInvocationError(
                "candidate credential-leak scan could not read a file",
                "credential_leak",
            ) from exc
    return leaks


def reject_credential_leak(
    workspace: Path,
    stdout: str,
    stderr: str,
    secrets: tuple[str, ...],
) -> None:
    if any(secret in stdout or secret in stderr for secret in secrets):
        raise AgentInvocationError(
            "agent output exposed authentication material",
            "credential_leak",
        )
    leaks = secret_leak_paths(workspace, secrets)
    if leaks:
        raise AgentInvocationError(
            "candidate artifact exposed authentication material",
            "credential_leak",
        )


def _atomic_private_json_write(path: Path, document: dict) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary_path.unlink(missing_ok=True)


def prepare_sanitized_claude_runtime(root: Path, auth_dir: Path) -> Path:
    """Create only the non-secret state needed for a non-interactive CLI call."""
    settings_path = auth_dir / "settings.json"
    state_path = root / "claude-state.json"
    _atomic_private_json_write(settings_path, SANITIZED_CLAUDE_SETTINGS)
    _atomic_private_json_write(state_path, SANITIZED_CLAUDE_STATE)
    return state_path


def persist_refreshed_claude_credentials(
    source: Path,
    disposable_copy: Path,
    before_document: dict,
) -> bool:
    """Persist only a structurally valid OAuth rotation from the Claude CLI.

    Confirmatory Claude calls are serialized, so one process owns the refresh
    token at a time. The coding agent sees only a disposable credential copy;
    after the CLI exits, only the validated OAuth record can flow back to the
    runner-owned source file. No task artifact can cross this boundary.
    """
    before = _claude_oauth(before_document)
    after_document = _read_private_json(disposable_copy)
    after = _claude_oauth(after_document)
    if after == before:
        return False

    mutable_fields = {"accessToken", "refreshToken", "expiresAt"}
    if set(after) != set(before) or any(
        after[field] != before[field] for field in set(before) - mutable_fields
    ):
        raise AgentInvocationError(
            "Claude credential refresh changed account metadata or schema",
            "authentication",
        )
    if after["accessToken"] == before["accessToken"]:
        raise AgentInvocationError(
            "Claude credential state changed without a new access token",
            "authentication",
        )
    if after["expiresAt"] <= before["expiresAt"]:
        raise AgentInvocationError(
            "Claude credential refresh did not advance token expiry",
            "authentication",
        )

    current_document = _read_private_json(source)
    current = _claude_oauth(current_document)
    if current != before:
        if current == after:
            return True
        raise AgentInvocationError(
            "Claude credential source changed during a serialized invocation",
            "authentication",
        )
    current_document["claudeAiOauth"] = after
    _atomic_private_json_write(source, current_document)
    return True


def persist_refreshed_codex_credentials(
    source: Path,
    disposable_copy: Path,
    before_document: dict,
) -> bool:
    """Persist only a structurally valid ChatGPT OAuth rotation from Codex."""
    before = _codex_chatgpt(before_document)
    after_document = _read_private_json(disposable_copy)
    after = _codex_chatgpt(after_document)
    if after_document == before_document:
        return False

    if set(after_document) != set(before_document) or any(
        after_document[field] != before_document[field]
        for field in set(before_document) - {"tokens", "last_refresh"}
    ):
        raise AgentInvocationError(
            "Codex credential refresh changed account metadata or schema",
            "authentication",
        )
    if set(after) != set(before) or after["account_id"] != before["account_id"]:
        raise AgentInvocationError(
            "Codex credential refresh changed account metadata or schema",
            "authentication",
        )
    if after["access_token"] == before["access_token"]:
        raise AgentInvocationError(
            "Codex credential state changed without a new access token",
            "authentication",
        )
    if _jwt_expiry(after["access_token"]) <= _jwt_expiry(before["access_token"]):
        raise AgentInvocationError(
            "Codex credential refresh did not advance token expiry",
            "authentication",
        )
    if _parse_utc_timestamp(
        after_document["last_refresh"], "last_refresh"
    ) <= _parse_utc_timestamp(before_document["last_refresh"], "last_refresh"):
        raise AgentInvocationError(
            "Codex credential refresh did not advance refresh time",
            "authentication",
        )

    current_document = _read_private_json(source)
    current = _codex_chatgpt(current_document)
    if current_document != before_document:
        if current_document == after_document:
            return True
        raise AgentInvocationError(
            "Codex credential source changed during a serialized invocation",
            "authentication",
        )
    if current != before:
        raise AgentInvocationError(
            "Codex credential source changed during a serialized invocation",
            "authentication",
        )
    current_document["tokens"] = after
    current_document["last_refresh"] = after_document["last_refresh"]
    _atomic_private_json_write(source, current_document)
    return True


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


def command_for(
    agent: str,
    workspace: Path,
    model: str | None,
    max_budget_usd: float,
    billing_mode: str = "unknown",
) -> list[str]:
    validate_billing_mode(billing_mode)
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
            "--output-format",
            "json",
        ]
        if billing_mode != "subscription":
            command.extend(["--max-budget-usd", str(max_budget_usd)])
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


MODEL_ALIASES = {"sonnet", "opus", "haiku", "session-default", "default"}


def claude_model_usage(stdout: str) -> dict[str, dict]:
    """Return the full per-model usage map the Claude CLI reported.

    The CLI bills auxiliary scaffolding calls to a second model, so this map
    is the disclosure record: every model that ran, not only the one that did
    the task. Callers must record it rather than collapse it.
    """
    try:
        event = json.loads(stdout)
    except json.JSONDecodeError:
        return {}
    model_usage = event.get("modelUsage")
    if not isinstance(model_usage, dict):
        return {}
    return {
        model: usage if isinstance(usage, dict) else {}
        for model, usage in model_usage.items()
        if isinstance(model, str)
    }


def reported_model(agent: str, stdout: str) -> str | None:
    """Return only a model identity explicitly reported by the CLI output."""
    if agent == "claude":
        try:
            event = json.loads(stdout)
        except json.JSONDecodeError:
            return None
        candidates = {
            model: usage
            for model, usage in claude_model_usage(stdout).items()
            if model not in MODEL_ALIASES
        }
        if len(candidates) == 1:
            return next(iter(candidates))
        if candidates:
            # The Claude CLI attributes auxiliary scaffolding calls to a
            # helper model alongside the model that performed the task. The
            # task model is the one that generated the completion, so attribute
            # by generated output and refuse a tie.
            ranked = sorted(
                candidates.items(),
                key=lambda item: int(item[1].get("outputTokens") or 0),
                reverse=True,
            )
            top_tokens = int(ranked[0][1].get("outputTokens") or 0)
            runner_up_tokens = int(ranked[1][1].get("outputTokens") or 0)
            if top_tokens > runner_up_tokens:
                return ranked[0][0]
            return None
        direct = event.get("model")
        if isinstance(direct, str) and direct not in MODEL_ALIASES:
            return direct
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


def _run_measurement_cycle_unlocked(
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
    persist_refreshed_credentials: bool = False,
) -> dict:
    """Run one real prompt cycle and retain only structured aggregate output."""
    if agent not in {"codex", "claude"}:
        raise ValueError(f"unsupported measurement agent: {agent}")
    if not auth_file.is_file():
        raise AgentInvocationError("authentication file is unreadable", "authentication")
    if agent == "claude" and state_file is not None:
        raise AgentInvocationError(
            "external Claude state files are prohibited; sanitized state is generated per call",
            "authentication",
        )
    if state_file is not None and not state_file.is_file():
        raise AgentInvocationError(f"state file is unreadable: {state_file}", "authentication")
    if not shutil.which("docker"):
        raise AgentInvocationError("docker executable not found", "isolation")

    credential_document = _read_private_json(auth_file)
    credential_secrets = credential_secret_values(credential_document)
    credential_before = None
    if persist_refreshed_credentials:
        credential_before = credential_document
        if agent == "claude":
            _claude_oauth(credential_before)
        else:
            _codex_chatgpt(credential_before)

    schema_path = workspace / ".loop-verdict-schema.json"
    schema_path.write_text(json.dumps(VERDICT_SCHEMA, separators=(",", ":")), encoding="utf-8")
    try:
        container_name = f"loop-measurement-{agent}-{os.getpid()}-{time.time_ns()}"
        started = time.perf_counter()
        credential_refresh_persisted = False
        with tempfile.TemporaryDirectory(prefix=f"loop-{agent}-auth-") as auth_root:
            auth_dir = Path(auth_root)
            auth_dir.chmod(0o700)
            auth_name = "auth.json" if agent == "codex" else ".credentials.json"
            auth_copy = auth_dir / auth_name
            shutil.copy2(auth_file, auth_copy)
            auth_copy.chmod(0o600)
            runtime_state_file = (
                prepare_sanitized_claude_runtime(auth_dir, auth_dir)
                if agent == "claude"
                else state_file
            )
            command = container_command_for(
                agent,
                workspace,
                model,
                max_budget_usd,
                container_image,
                state_file=runtime_state_file,
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
            timeout_error = None
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
                timeout_error = exc
            finally:
                if credential_before is not None:
                    persister = (
                        persist_refreshed_claude_credentials
                        if agent == "claude"
                        else persist_refreshed_codex_credentials
                    )
                    credential_refresh_persisted = persister(
                        auth_file, auth_copy, credential_before
                    )
                credential_after = _read_private_json(auth_copy)
                credential_secrets = tuple(
                    sorted(
                        set(credential_secrets)
                        | set(credential_secret_values(credential_after))
                    )
                )
            if timeout_error is not None:
                timeout_stdout = timeout_error.stdout or ""
                timeout_stderr = timeout_error.stderr or ""
                if isinstance(timeout_stdout, bytes):
                    timeout_stdout = timeout_stdout.decode("utf-8", errors="replace")
                if isinstance(timeout_stderr, bytes):
                    timeout_stderr = timeout_stderr.decode("utf-8", errors="replace")
                reject_credential_leak(
                    workspace,
                    timeout_stdout,
                    timeout_stderr,
                    credential_secrets,
                )
                raise AgentInvocationError("agent invocation timed out", "timeout") from timeout_error
    finally:
        schema_path.unlink(missing_ok=True)

    reject_credential_leak(
        workspace,
        process.stdout,
        process.stderr,
        credential_secrets,
    )
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
        "model_usage_breakdown": (
            claude_model_usage(process.stdout) if agent == "claude" else {}
        ),
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
        "credential_refresh_persisted": credential_refresh_persisted,
        "credential_leak_scan_passed": True,
    }


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
    persist_refreshed_credentials: bool = False,
) -> dict:
    """Run one cycle with one refresh writer per subscription credential."""
    credential_locks = {
        "claude": CLAUDE_CREDENTIAL_LOCK,
        "codex": CODEX_CREDENTIAL_LOCK,
    }
    lock = credential_locks[agent] if persist_refreshed_credentials else nullcontext()
    with lock:
        return _run_measurement_cycle_unlocked(
            agent=agent,
            model=model,
            task=task,
            workspace=workspace,
            cycle=cycle,
            feedback=feedback,
            container_image=container_image,
            auth_file=auth_file,
            state_file=state_file,
            timeout_seconds=timeout_seconds,
            billing_mode=billing_mode,
            max_budget_usd=max_budget_usd,
            reasoning_effort=reasoning_effort,
            persist_refreshed_credentials=persist_refreshed_credentials,
        )


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


def write_json_exclusive(path: Path, value: dict) -> None:
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    except FileExistsError as exc:
        raise FileExistsError(f"refusing to overwrite adapter evidence: {path}") from exc
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _run_smoke_unlocked(
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
    persist_refreshed_credentials: bool = False,
) -> dict:
    validate_billing_mode(billing_mode)
    if container_image and not auth_file and not auth_env:
        raise ValueError("--container-image requires --auth-file or --auth-env")
    if auth_file and not auth_file.is_file():
        raise ValueError("--auth-file must be readable")
    if auth_env and auth_env not in os.environ:
        raise ValueError(f"authentication environment variable is unset: {auth_env}")
    if agent == "claude" and state_file is not None:
        raise ValueError(
            "--state-file is prohibited; sanitized Claude state is generated per call"
        )
    if state_file and not state_file.is_file():
        raise ValueError("--state-file must be readable")
    executable = "docker" if container_image else agent
    if not shutil.which(executable):
        raise RuntimeError(f"{agent} executable not found")

    credential_document = _read_private_json(auth_file) if auth_file else None
    credential_secrets = (
        credential_secret_values(credential_document)
        if credential_document is not None
        else ()
    )
    credential_before = None
    if persist_refreshed_credentials:
        if not container_image or auth_file is None:
            raise ValueError(
                "--persist-refreshed-credentials requires a containerized agent and --auth-file"
            )
        credential_before = credential_document
        if agent == "claude":
            _claude_oauth(credential_before)
        else:
            _codex_chatgpt(credential_before)

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
        runtime_state_file = (
            prepare_sanitized_claude_runtime(Path(temp_root), docker_auth_dir)
            if agent == "claude" and docker_auth_dir is not None
            else state_file
        )
        docker_auth_env_file = None
        if container_image and auth_env:
            docker_auth_env_file = Path(temp_root) / "docker-auth.env"
            docker_auth_env_file.write_text(
                f"{auth_env}={os.environ[auth_env]}\n", encoding="utf-8"
            )
            docker_auth_env_file.chmod(0o600)

        started = time.perf_counter()
        container_name = (
            f"loop-smoke-{agent}-{os.getpid()}-{time.time_ns()}"
            if container_image
            else None
        )
        command = (
            container_command_for(
                agent,
                workspace,
                model,
                max_budget_usd,
                container_image,
                None if docker_auth_dir else auth_file,
                None if docker_auth_env_file else auth_env,
                runtime_state_file,
                docker_auth_env_file,
                docker_auth_dir,
                container_name=container_name,
            )
            if container_image
            else command_for(
                agent,
                workspace,
                model,
                max_budget_usd,
                billing_mode=billing_mode,
            )
        )
        credential_refresh_persisted = False
        timeout_error = None
        try:
            process = subprocess.run(
                command,
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            if container_name is not None:
                _bounded_docker_cleanup(container_name)
            timeout_error = exc
        finally:
            if credential_before is not None:
                persister = (
                    persist_refreshed_claude_credentials
                    if agent == "claude"
                    else persist_refreshed_codex_credentials
                )
                credential_refresh_persisted = persister(
                    auth_file, auth_copy, credential_before
                )
            if docker_auth_dir is not None:
                credential_after = _read_private_json(auth_copy)
                credential_secrets = tuple(
                    sorted(
                        set(credential_secrets)
                        | set(credential_secret_values(credential_after))
                    )
                )
        if timeout_error is not None:
            timeout_stdout = timeout_error.stdout or ""
            timeout_stderr = timeout_error.stderr or ""
            if isinstance(timeout_stdout, bytes):
                timeout_stdout = timeout_stdout.decode("utf-8", errors="replace")
            if isinstance(timeout_stderr, bytes):
                timeout_stderr = timeout_stderr.decode("utf-8", errors="replace")
            if credential_secrets:
                reject_credential_leak(
                    workspace,
                    timeout_stdout,
                    timeout_stderr,
                    credential_secrets,
                )
            raise AgentInvocationError("agent invocation timed out", "timeout") from timeout_error
        if credential_secrets:
            reject_credential_leak(
                workspace,
                process.stdout,
                process.stderr,
                credential_secrets,
            )
        agent_seconds = time.perf_counter() - started

        public_tests = run_public_tests(workspace)
        final, final_seconds = se_experiment.run_oracle(task, workspace)
        usage = parse_codex_usage(process.stdout) if agent == "codex" else parse_claude_usage(process.stdout)
        model_served = reported_model(agent, process.stdout)
        return {
            "schema_version": 2,
            "status": "adapter smoke test; not a research result",
            "agent": agent,
            "model_requested": model or "session-default",
            "model_served": model_served,
            "model_usage_breakdown": (
                claude_model_usage(process.stdout) if agent == "claude" else {}
            ),
            "model_identity_evidence": (
                "runtime_cli_output" if model_served else "unreported"
            ),
            "task": task,
            "process_returncode": process.returncode,
            "credential_refresh_persisted": credential_refresh_persisted,
            "credential_leak_scan_passed": bool(credential_secrets),
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
    persist_refreshed_credentials: bool = False,
) -> dict:
    """Run an adapter smoke with one refresh writer per subscription credential."""
    credential_locks = {
        "claude": CLAUDE_CREDENTIAL_LOCK,
        "codex": CODEX_CREDENTIAL_LOCK,
    }
    lock = credential_locks[agent] if persist_refreshed_credentials else nullcontext()
    with lock:
        return _run_smoke_unlocked(
            agent,
            task,
            model,
            timeout_seconds,
            max_budget_usd,
            container_image,
            auth_file,
            auth_env,
            state_file,
            billing_mode,
            persist_refreshed_credentials,
        )


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
    parser.add_argument("--persist-refreshed-credentials", action="store_true")
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
        persist_refreshed_credentials=args.persist_refreshed_credentials,
    )
    write_json_exclusive(args.output, output)
    print(
        f"{args.agent}: returncode={output['process_returncode']} "
        f"public_tests={output['public_tests']['passed']} gain={output['real_gain']}"
    )


if __name__ == "__main__":
    main()
