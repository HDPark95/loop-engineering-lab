#!/usr/bin/env python3
"""Drive one ephemeral Codex app-server turn and emit aggregate evidence only.

The measurement runner needs two facts that ``codex exec --json`` does not
currently emit together: the effective runtime model and a structured final
message. App-server v2 returns the effective model at ``thread/start``, emits
settings changes and explicit model reroutes, and supports a per-turn output
schema. This client consumes that stream inside the agent container and prints
one normalized JSON object; raw model text and protocol events never leave the
container.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _send(process: subprocess.Popen, message: dict) -> None:
    assert process.stdin is not None
    process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
    process.stdin.flush()


def _read(process: subprocess.Popen) -> dict:
    assert process.stdout is not None
    line = process.stdout.readline()
    if not line:
        raise RuntimeError(f"app-server exited before completion (status {process.poll()})")
    try:
        message = json.loads(line)
    except json.JSONDecodeError as exc:
        raise RuntimeError("app-server emitted non-JSON protocol output") from exc
    if not isinstance(message, dict):
        raise RuntimeError("app-server emitted a non-object protocol message")
    return message


def _wait_response(process: subprocess.Popen, request_id: int, events: list[dict]) -> dict:
    while True:
        message = _read(process)
        if message.get("id") == request_id and ("result" in message or "error" in message):
            if message.get("error") is not None:
                error = message["error"]
                raise RuntimeError(f"app-server request {request_id} failed: {error}")
            result = message.get("result")
            if not isinstance(result, dict):
                raise RuntimeError(f"app-server request {request_id} omitted an object result")
            return result
        events.append(message)


def summarize(
    requested_model: str,
    thread_start: dict,
    events: list[dict],
) -> dict:
    """Reduce protocol events to the evidence retained in the research log."""
    effective_models = []
    start_model = thread_start.get("model")
    if isinstance(start_model, str) and start_model:
        effective_models.append(start_model)

    reasoning_efforts = []
    start_effort = thread_start.get("reasoningEffort")
    if isinstance(start_effort, str) and start_effort:
        reasoning_efforts.append(start_effort)

    reroutes = []
    report = None
    usage = None
    request_usages = []
    observed_totals = set()
    turn_status = None
    for event in events:
        method = event.get("method")
        params = event.get("params") or {}
        if method == "thread/settings/updated":
            settings = params.get("threadSettings") or {}
            model = settings.get("model")
            if isinstance(model, str) and model:
                effective_models.append(model)
            effort = settings.get("effort") or settings.get("reasoningEffort")
            if isinstance(effort, str) and effort:
                reasoning_efforts.append(effort)
        elif method == "model/rerouted":
            reroutes.append(
                {
                    "from_model": params.get("fromModel"),
                    "to_model": params.get("toModel"),
                    "reason": params.get("reason"),
                }
            )
        elif method == "thread/tokenUsage/updated":
            token_usage = params.get("tokenUsage") or {}
            total = token_usage.get("total") or token_usage.get("last")
            last = token_usage.get("last")
            usage = total
            if isinstance(total, dict):
                total_key = tuple(
                    total.get(field)
                    for field in ("inputTokens", "cachedInputTokens", "outputTokens")
                )
                if total_key not in observed_totals and isinstance(last, dict):
                    request_usages.append(
                        {
                            "input_tokens": last.get("inputTokens"),
                            "cached_input_tokens": last.get("cachedInputTokens"),
                            "output_tokens": last.get("outputTokens"),
                        }
                    )
                    observed_totals.add(total_key)
        elif method == "item/completed":
            item = params.get("item") or {}
            if item.get("type") == "agentMessage":
                try:
                    candidate = json.loads(item.get("text", ""))
                except (TypeError, json.JSONDecodeError):
                    candidate = None
                if isinstance(candidate, dict):
                    report = candidate
        elif method == "turn/completed":
            turn = params.get("turn") or {}
            turn_status = turn.get("status")

    if reroutes:
        served_model = reroutes[-1].get("to_model")
        evidence = "app_server_model_rerouted"
    elif len(set(effective_models)) == 1:
        served_model = effective_models[0]
        evidence = "app_server_effective_model_no_reroute"
    else:
        served_model = None
        evidence = "app_server_model_ambiguous"

    input_tokens = usage.get("inputTokens") if isinstance(usage, dict) else None
    output_tokens = usage.get("outputTokens") if isinstance(usage, dict) else None
    cached_tokens = usage.get("cachedInputTokens") if isinstance(usage, dict) else None
    return {
        "protocol": "codex-app-server-v2",
        "turn_status": turn_status,
        "self_report": report,
        "model_requested": requested_model,
        "model_served": served_model,
        "model_identity_evidence": evidence,
        "model_reroutes": reroutes,
        "effective_models_observed": sorted(set(effective_models)),
        "reasoning_efforts_observed": sorted(set(reasoning_efforts)),
        "reasoning_effort_served": reasoning_efforts[-1] if reasoning_efforts else None,
        "usage": {
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_tokens,
            "output_tokens": output_tokens,
            "request_usages": request_usages,
        },
    }


def run(args: argparse.Namespace) -> dict:
    schema = json.loads(Path(args.output_schema).read_text(encoding="utf-8"))
    process = subprocess.Popen(
        ["codex", "app-server", "--stdio", "--strict-config"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    events: list[dict] = []
    try:
        _send(
            process,
            {
                "method": "initialize",
                "id": 0,
                "params": {
                    "clientInfo": {
                        "name": "loop_engineering_measurement",
                        "title": "LOOP Engineering Measurement",
                        "version": "1.0.0",
                    },
                    "capabilities": {"experimentalApi": True},
                },
            },
        )
        _wait_response(process, 0, events)
        _send(process, {"method": "initialized", "params": {}})

        thread_params = {
            "cwd": args.workspace,
            "model": args.model,
            "approvalPolicy": "never",
            "sandbox": "danger-full-access",
            "ephemeral": True,
        }
        _send(process, {"method": "thread/start", "id": 1, "params": thread_params})
        thread_start = _wait_response(process, 1, events)
        thread = thread_start.get("thread") or {}
        thread_id = thread.get("id")
        if not isinstance(thread_id, str) or not thread_id:
            raise RuntimeError("thread/start omitted a thread id")

        turn_params = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": args.prompt}],
            "cwd": args.workspace,
            "model": args.model,
            "approvalPolicy": "never",
            "sandboxPolicy": {"type": "dangerFullAccess"},
            "outputSchema": schema,
        }
        if args.reasoning_effort:
            turn_params["effort"] = args.reasoning_effort
        _send(process, {"method": "turn/start", "id": 2, "params": turn_params})
        turn_start = _wait_response(process, 2, events)
        turn = turn_start.get("turn") or {}
        turn_id = turn.get("id")
        while True:
            event = _read(process)
            events.append(event)
            if event.get("method") != "turn/completed":
                continue
            completed_turn = (event.get("params") or {}).get("turn") or {}
            if turn_id is None or completed_turn.get("id") == turn_id:
                break
        result = summarize(args.model, thread_start, events)
        if result["turn_status"] != "completed":
            raise RuntimeError(f"Codex turn ended with status {result['turn_status']!r}")
        if result["self_report"] is None:
            raise RuntimeError("Codex turn omitted the structured final report")
        if result["usage"]["input_tokens"] is None or result["usage"]["output_tokens"] is None:
            raise RuntimeError("Codex app-server omitted token usage")
        return result
    finally:
        if process.stdin is not None:
            process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--workspace", default="/workspace")
    parser.add_argument("--reasoning-effort")
    parser.add_argument("--output-schema", required=True)
    parser.add_argument("--prompt", required=True)
    args = parser.parse_args()
    try:
        result = run(args)
    except Exception as exc:  # noqa: BLE001 - normalize container failures
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
