#!/usr/bin/env python3
"""Validate runtime evidence and fill the two pre-freeze manifest templates."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
import tempfile
from pathlib import Path

import finalize_measurement_manifest
import replay
import run_measurement


ROOT = Path(__file__).resolve().parent
OFFICIAL_CLAUDE_PRICING_URL = "https://platform.claude.com/docs/en/about-claude/pricing"
CODEX_REFERENCE_LOG = (
    ROOT
    / "logs/apparatus/codex-terra-appserver-s1repo-publictests-20260813.cycles.jsonl"
)
CODEX_REFERENCE_LOG_SHA256 = (
    "7f53d641513bc17348780d65d70655f60d3bcb70e34627106ed18d79624b4934"
)
CODEX_ALL_LONG_CONTEXT_FACTOR = 2.5
SAFETY_FACTOR = 4.0
MINIMUM_SHADOW_USD = 20.0


def fail(message: str) -> None:
    raise RuntimeError(message)


def file_sha256(path: Path) -> str:
    return replay.file_sha256(path)


def read_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid JSON object: {path}") from exc
    if not isinstance(value, dict):
        fail(f"JSON root must be an object: {path}")
    return value


def finite_nonnegative(value: object, field: str) -> float:
    if isinstance(value, bool):
        fail(f"{field} must be finite and nonnegative")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{field} must be finite and nonnegative") from exc
    if not math.isfinite(number) or number < 0:
        fail(f"{field} must be finite and nonnegative")
    return number


def validate_smoke(smoke: dict, expected_requested: str | None = None) -> str:
    if smoke.get("schema_version") != 2 or smoke.get("agent") != "claude":
        fail("Claude smoke evidence must use adapter schema 2")
    if expected_requested is not None and smoke.get("model_requested") != expected_requested:
        fail(f"Claude smoke did not request {expected_requested!r}")
    model = smoke.get("model_served")
    if not isinstance(model, str) or not model or model in {
        "sonnet",
        "opus",
        "haiku",
        "session-default",
        "default",
    }:
        fail("Claude smoke did not report an exact served model ID")
    if smoke.get("process_returncode") != 0:
        fail("Claude smoke process did not exit successfully")
    execution = smoke.get("execution")
    if not isinstance(execution, dict) or execution.get("model_completed") is not True:
        fail("Claude smoke did not complete a model invocation")
    public_tests = smoke.get("public_tests")
    if not isinstance(public_tests, dict) or public_tests.get("passed") is not True:
        fail("Claude smoke candidate did not pass public tests")
    if smoke.get("credential_leak_scan_passed") is not True:
        fail("Claude smoke credential-leak scan did not pass")
    return model


def validate_runtime_evidence(
    alias_smoke_path: Path, exact_smoke_path: Path, pricing_path: Path
) -> tuple[str, dict, dict]:
    alias_smoke = read_object(alias_smoke_path)
    exact_smoke = read_object(exact_smoke_path)
    alias_model = validate_smoke(alias_smoke, "sonnet")
    exact_model = validate_smoke(exact_smoke, alias_model)
    if alias_model != exact_model:
        fail("alias and exact Claude probes served different models")

    pricing = read_object(pricing_path)
    required = {
        "schema_version",
        "model",
        "pricing_schedule_id",
        "pricing_source_url",
        "pricing_retrieved_utc",
        "usd_per_1k_input",
        "usd_per_1k_cached_input",
        "usd_per_1k_output",
        "cache_write_input_multiplier",
        "cache_write_1h_input_multiplier",
        "long_context_threshold_input_tokens",
        "long_context_input_multiplier",
        "long_context_output_multiplier",
    }
    if set(pricing) != required or pricing.get("schema_version") != 1:
        fail("Claude pricing record has the wrong schema or field set")
    if pricing["model"] != exact_model:
        fail("Claude pricing record model does not match runtime evidence")
    if pricing["pricing_source_url"] != OFFICIAL_CLAUDE_PRICING_URL:
        fail("Claude pricing record must cite the frozen official pricing URL")
    retrieved = str(pricing["pricing_retrieved_utc"])
    try:
        parsed_retrieved = datetime.datetime.strptime(retrieved, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        parsed_retrieved = None
    if (
        parsed_retrieved is None
        or parsed_retrieved.strftime("%Y-%m-%dT%H:%M:%SZ") != retrieved
    ):
        fail("Claude pricing retrieval time must be second-precision UTC")
    if not isinstance(pricing["pricing_schedule_id"], str) or not pricing[
        "pricing_schedule_id"
    ].strip():
        fail("Claude pricing schedule ID must be nonempty")
    for field in (
        "usd_per_1k_input",
        "usd_per_1k_cached_input",
        "usd_per_1k_output",
    ):
        finite_nonnegative(pricing[field], field)
    if (
        float(pricing["usd_per_1k_input"]) <= 0
        or float(pricing["usd_per_1k_output"]) <= 0
        or float(pricing["usd_per_1k_cached_input"])
        > float(pricing["usd_per_1k_input"])
    ):
        fail("Claude pricing rates are not a valid positive cache-discount schedule")
    for field in (
        "cache_write_input_multiplier",
        "cache_write_1h_input_multiplier",
        "long_context_input_multiplier",
        "long_context_output_multiplier",
    ):
        if finite_nonnegative(pricing[field], field) < 1.0:
            fail(f"{field} must be at least one")
    threshold = pricing["long_context_threshold_input_tokens"]
    if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold <= 0:
        fail("long-context threshold must be a positive integer")
    return exact_model, pricing, {
        "alias_smoke_sha256": file_sha256(alias_smoke_path),
        "exact_smoke_sha256": file_sha256(exact_smoke_path),
        "pricing_record_sha256": file_sha256(pricing_path),
    }


def replace_values(value: object, replacements: dict[str, object]) -> object:
    if isinstance(value, dict):
        return {key: replace_values(child, replacements) for key, child in value.items()}
    if isinstance(value, list):
        return [replace_values(child, replacements) for child in value]
    if isinstance(value, str) and value in replacements:
        return replacements[value]
    return value


def canonical_json_bytes(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def write_json_exclusive(path: Path, value: dict) -> None:
    payload = canonical_json_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    except FileExistsError as exc:
        raise RuntimeError(f"refusing to overwrite runtime evidence: {path}") from exc
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def validate_manifest_data(output_path: Path, manifest: dict) -> None:
    """Validate manifest bytes from the directory where the output will live."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{output_path.name}.validation.",
        suffix=".json",
        dir=output_path.parent,
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        run_measurement.load_manifest(temporary_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def apparatus_replacements(exact_model: str, pricing: dict) -> dict[str, object]:
    return {
        "__CLAUDE_EXACT_MODEL__": exact_model,
        "__CLAUDE_USD_PER_1K_INPUT__": pricing["usd_per_1k_input"],
        "__CLAUDE_USD_PER_1K_OUTPUT__": pricing["usd_per_1k_output"],
    }


def build_apparatus_manifest(
    template_path: Path,
    output_path: Path,
    alias_smoke_path: Path,
    exact_smoke_path: Path,
    pricing_path: Path,
) -> dict:
    exact_model, pricing, _evidence = validate_runtime_evidence(
        alias_smoke_path, exact_smoke_path, pricing_path
    )
    template = read_object(template_path)
    filled = replace_values(template, apparatus_replacements(exact_model, pricing))
    unresolved = finalize_measurement_manifest.unresolved_value_sentinels(filled)
    if unresolved:
        fail("apparatus manifest still has runtime placeholders: " + ", ".join(unresolved))
    if filled.get("apparatus_test") is not True:
        fail("apparatus manifest must remain apparatus-only")
    validate_manifest_data(output_path, filled)
    write_json_exclusive(output_path, filled)
    return filled


def validate_apparatus_manifest_binding(
    path: Path, exact_model: str, pricing: dict
) -> tuple[dict, str]:
    manifest = run_measurement.load_manifest(path)
    agents = manifest.get("agents", [])
    if (
        manifest.get("apparatus_test") is not True
        or len(agents) != 1
        or agents[0].get("name") != "claude"
        or agents[0].get("adapter") != "claude"
        or agents[0].get("model") != exact_model
        or agents[0].get("usd_per_1k_input") != pricing["usd_per_1k_input"]
        or agents[0].get("usd_per_1k_output") != pricing["usd_per_1k_output"]
    ):
        fail("Claude resource apparatus manifest does not match runtime evidence")
    return manifest, run_measurement.manifest_digest(manifest)


def completed_apparatus_rows(
    path: Path, model: str, expected_manifest_digest: str
) -> list[dict]:
    cycles, abandoned, unparsable = replay.load(path)
    if abandoned or unparsable:
        fail("Claude resource apparatus log contains abandoned or corrupt rows")
    if len(cycles) != 6 or {row.get("cycle") for row in cycles} != set(range(1, 7)):
        fail("Claude resource apparatus must contain exactly cycles 1 through 6")
    expected_trajectory = (
        f"s1_swebench|claude|{model}|grounded-numeric|11"
    )
    attempts = {row.get("attempt_id") for row in cycles}
    if len(attempts) != 1 or not next(iter(attempts), None):
        fail("Claude resource apparatus must contain one complete attempt")
    for row in cycles:
        if (
            row.get("schema_version") != run_measurement.SCHEMA_VERSION
            or row.get("apparatus_test") is not True
            or row.get("task") != "s1_swebench"
            or row.get("agent") != "claude"
            or row.get("seed") != 11
            or row.get("cell") != "grounded-numeric"
            or row.get("trajectory") != expected_trajectory
            or row.get("cycles_planned") != 6
            or row.get("manifest_digest") != expected_manifest_digest
            or row.get("cost_allocation_fraction") != 1.0
            or row.get("model_served") != model
            or row.get("model_identity_matches") is not True
            or row.get("credential_leak_scan_passed") is not True
        ):
            fail("Claude resource apparatus row violates the frozen apparatus contract")
    return cycles


def parse_utc(value: object, field: str) -> datetime.datetime:
    if not isinstance(value, str):
        fail(f"{field} must be second-precision UTC")
    try:
        parsed = datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise RuntimeError(f"{field} must be second-precision UTC") from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        fail(f"{field} must be second-precision UTC")
    return parsed


def validate_resource_record(path: Path, rows: list[dict]) -> dict:
    record = read_object(path)
    required = {
        "schema_version",
        "status",
        "started_utc",
        "finished_utc",
        "elapsed_seconds",
        "architecture",
        "cpu_count",
        "samples",
        "docker_stats_failures",
        "container_observations",
        "peak_concurrent_containers",
        "peak_single_container_memory_bytes",
        "peak_total_container_memory_bytes",
        "peak_container_cpu_percent",
        "host_memory_total_bytes",
        "minimum_host_memory_available_bytes",
        "host_swap_total_bytes",
        "peak_host_swap_used_bytes",
        "peak_host_load_1m",
        "passed",
    }
    if set(record) != required or record.get("schema_version") != 1:
        fail("Claude resource record has the wrong schema or field set")
    if (
        record.get("status")
        != "apparatus resource observation; not a research result"
        or record.get("passed") is not True
        or record.get("architecture") != "x86_64"
        or record.get("cpu_count") != 8
    ):
        fail("Claude resource record violates the registered host contract")
    integer_fields = (
        "samples",
        "docker_stats_failures",
        "container_observations",
        "peak_concurrent_containers",
        "peak_single_container_memory_bytes",
        "peak_total_container_memory_bytes",
        "host_memory_total_bytes",
        "minimum_host_memory_available_bytes",
        "host_swap_total_bytes",
        "peak_host_swap_used_bytes",
    )
    for field in integer_fields:
        value = record[field]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            fail(f"Claude resource record has invalid {field}")
    for field in ("elapsed_seconds", "peak_container_cpu_percent", "peak_host_load_1m"):
        finite_nonnegative(record[field], field)
    if (
        record["samples"] <= 0
        or record["docker_stats_failures"] != 0
        or record["container_observations"] <= 0
        or record["peak_concurrent_containers"] != 1
        or record["peak_single_container_memory_bytes"] <= 0
        or record["peak_total_container_memory_bytes"]
        != record["peak_single_container_memory_bytes"]
        or not 30_000_000_000 <= record["host_memory_total_bytes"] <= 40_000_000_000
        or not 0 < record["minimum_host_memory_available_bytes"]
        <= record["host_memory_total_bytes"]
        or record["peak_host_swap_used_bytes"] > record["host_swap_total_bytes"]
    ):
        fail("Claude resource record did not capture a clean single-lane apparatus")
    started = parse_utc(record["started_utc"], "resource started_utc")
    finished = parse_utc(record["finished_utc"], "resource finished_utc")
    row_times = [parse_utc(row.get("wall_clock_utc"), "apparatus wall_clock_utc") for row in rows]
    if not started < finished or not started <= min(row_times) <= max(row_times) <= finished:
        fail("Claude resource observation does not enclose the apparatus log")
    elapsed = finite_nonnegative(record["elapsed_seconds"], "elapsed_seconds")
    timestamp_elapsed = (finished - started).total_seconds()
    if elapsed <= 0 or abs(elapsed - timestamp_elapsed) > 2.0:
        fail("Claude resource elapsed time disagrees with its UTC interval")
    return record


def all_long_context_upper(row: dict, pricing: dict) -> float:
    uncached = finite_nonnegative(row.get("uncached_input_tokens"), "uncached tokens")
    cached = finite_nonnegative(row.get("cached_input_tokens"), "cached tokens")
    output = finite_nonnegative(row.get("output_tokens"), "output tokens")
    writes_5m = finite_nonnegative(
        row.get("cache_write_5m_input_tokens"), "5m cache-write tokens"
    )
    writes_1h = finite_nonnegative(
        row.get("cache_write_1h_input_tokens"), "1h cache-write tokens"
    )
    if row.get("cache_write_input_tokens_exact") is not True:
        fail("Claude apparatus must report exact cache-write token counts")
    if writes_5m + writes_1h != finite_nonnegative(
        row.get("cache_write_input_tokens"), "cache-write tokens"
    ):
        fail("Claude apparatus cache-write TTL counts are inconsistent")
    total_input = finite_nonnegative(row.get("input_tokens"), "total input tokens")
    if total_input != uncached + cached or writes_5m + writes_1h > uncached:
        fail("Claude apparatus normalized input token counts are inconsistent")
    input_rate = float(pricing["usd_per_1k_input"]) / 1000.0
    cached_rate = float(pricing["usd_per_1k_cached_input"]) / 1000.0
    output_rate = float(pricing["usd_per_1k_output"]) / 1000.0
    input_component = (
        uncached * input_rate
        + cached * cached_rate
        + writes_5m
        * input_rate
        * (float(pricing["cache_write_input_multiplier"]) - 1.0)
        + writes_1h
        * input_rate
        * (float(pricing["cache_write_1h_input_multiplier"]) - 1.0)
    ) * float(pricing["long_context_input_multiplier"])
    output_component = (
        output
        * output_rate
        * float(pricing["long_context_output_multiplier"])
    )
    return input_component + output_component


def codex_reference_upper() -> float:
    if file_sha256(CODEX_REFERENCE_LOG) != CODEX_REFERENCE_LOG_SHA256:
        fail("Codex S1 reference apparatus log digest changed")
    cycles, abandoned, unparsable = replay.load(CODEX_REFERENCE_LOG)
    if (
        abandoned
        or unparsable
        or len(cycles) != 6
        or {row.get("cycle") for row in cycles} != set(range(1, 7))
    ):
        fail("Codex S1 reference apparatus log is incomplete")
    attempts = {row.get("attempt_id") for row in cycles}
    if len(attempts) != 1 or not next(iter(attempts), None):
        fail("Codex S1 reference apparatus must contain one complete attempt")
    for row in cycles:
        if (
            row.get("apparatus_test") is not True
            or row.get("task") != "s1_swebench"
            or row.get("agent") != "codex"
            or row.get("model_served") != "gpt-5.6-terra"
            or row.get("model_identity_matches") is not True
            or row.get("cell") != "grounded-numeric"
            or row.get("seed") != 11
            or row.get("cost_allocation_fraction") != 1.0
        ):
            fail("Codex S1 reference apparatus violates the frozen contract")
    base = sum(
        finite_nonnegative(row.get("api_equivalent_usd"), "Codex base shadow USD")
        for row in cycles
    )
    return base * CODEX_ALL_LONG_CONTEXT_FACTOR


def conservative_estimate(claude_upper: float, codex_upper: float) -> float:
    return float(
        math.ceil(
            max(
                MINIMUM_SHADOW_USD,
                SAFETY_FACTOR * claude_upper,
                SAFETY_FACTOR * codex_upper,
            )
        )
    )


def confirmatory_replacements(
    exact_model: str, pricing: dict, estimate: float
) -> dict[str, object]:
    return {
        **apparatus_replacements(exact_model, pricing),
        "__CLAUDE_USD_PER_1K_CACHED_INPUT__": pricing["usd_per_1k_cached_input"],
        "__CLAUDE_LONG_CONTEXT_THRESHOLD_INPUT_TOKENS__": pricing[
            "long_context_threshold_input_tokens"
        ],
        "__CLAUDE_LONG_CONTEXT_INPUT_MULTIPLIER__": pricing[
            "long_context_input_multiplier"
        ],
        "__CLAUDE_LONG_CONTEXT_OUTPUT_MULTIPLIER__": pricing[
            "long_context_output_multiplier"
        ],
        "__CLAUDE_PRICING_SCHEDULE_ID__": pricing["pricing_schedule_id"],
        "__CLAUDE_PRICING_RETRIEVED_UTC__": pricing["pricing_retrieved_utc"],
        "__ESTIMATED_API_EQUIVALENT_USD_PER_TRAJECTORY__": estimate,
    }


def validate_confirmatory_pricing_binding(
    manifest: dict, exact_model: str, pricing: dict
) -> None:
    claude_agents = [
        agent for agent in manifest.get("agents", []) if agent.get("name") == "claude"
    ]
    if len(claude_agents) != 1:
        fail("confirmatory template must contain exactly one Claude agent")
    claude = claude_agents[0]
    expected = {
        "model": exact_model,
        "pricing_schedule_id": pricing["pricing_schedule_id"],
        "pricing_source_url": pricing["pricing_source_url"],
        "pricing_retrieved_utc": pricing["pricing_retrieved_utc"],
        "usd_per_1k_input": pricing["usd_per_1k_input"],
        "usd_per_1k_cached_input": pricing["usd_per_1k_cached_input"],
        "usd_per_1k_output": pricing["usd_per_1k_output"],
        "cache_write_input_multiplier": pricing["cache_write_input_multiplier"],
        "cache_write_1h_input_multiplier": pricing[
            "cache_write_1h_input_multiplier"
        ],
        "long_context_threshold_input_tokens": pricing[
            "long_context_threshold_input_tokens"
        ],
        "long_context_input_multiplier": pricing[
            "long_context_input_multiplier"
        ],
        "long_context_output_multiplier": pricing[
            "long_context_output_multiplier"
        ],
    }
    mismatches = [field for field, value in expected.items() if claude.get(field) != value]
    if mismatches:
        fail(
            "confirmatory template does not reproduce the official Claude pricing "
            "record: "
            + ", ".join(mismatches)
        )


def build_confirmatory_template(args: argparse.Namespace) -> dict:
    if args.output.resolve() == args.evidence_output.resolve():
        fail("confirmatory template and evidence outputs must be distinct")
    exact_model, pricing, evidence_hashes = validate_runtime_evidence(
        args.alias_smoke, args.exact_smoke, args.pricing
    )
    _apparatus_manifest, apparatus_manifest_digest = (
        validate_apparatus_manifest_binding(
            args.claude_manifest, exact_model, pricing
        )
    )
    rows = completed_apparatus_rows(
        args.claude_log, exact_model, apparatus_manifest_digest
    )
    validate_resource_record(args.claude_resources, rows)
    claude_upper = sum(all_long_context_upper(row, pricing) for row in rows)
    codex_upper = codex_reference_upper()
    estimate = conservative_estimate(claude_upper, codex_upper)
    template = read_object(args.template)
    filled = replace_values(
        template, confirmatory_replacements(exact_model, pricing, estimate)
    )
    validate_confirmatory_pricing_binding(filled, exact_model, pricing)
    unresolved = finalize_measurement_manifest.unresolved_value_sentinels(filled)
    if unresolved:
        fail("confirmatory template still has runtime placeholders: " + ", ".join(unresolved))
    if filled.get("preregistration_commit") != finalize_measurement_manifest.FREEZE_SENTINEL:
        fail("confirmatory template lost the freeze-commit sentinel")
    validation_copy = dict(filled)
    validation_copy["preregistration_commit"] = "0" * 40
    validate_manifest_data(args.output, validation_copy)
    evidence = {
        "schema_version": 1,
        "exact_claude_model": exact_model,
        **evidence_hashes,
        "claude_apparatus_manifest_sha256": file_sha256(args.claude_manifest),
        "claude_apparatus_manifest_digest": apparatus_manifest_digest,
        "claude_apparatus_log_sha256": file_sha256(args.claude_log),
        "claude_apparatus_resources_sha256": file_sha256(args.claude_resources),
        "codex_reference_log_sha256": CODEX_REFERENCE_LOG_SHA256,
        "claude_all_long_context_upper_usd": round(claude_upper, 6),
        "codex_base_to_all_long_context_factor": CODEX_ALL_LONG_CONTEXT_FACTOR,
        "codex_all_long_context_upper_usd": round(codex_upper, 6),
        "safety_factor": SAFETY_FACTOR,
        "minimum_shadow_usd": MINIMUM_SHADOW_USD,
        "estimated_api_equivalent_usd_per_trajectory": estimate,
        "formula": "ceil(max(minimum, safety*claude_all_long_upper, safety*codex_all_long_upper))",
        "filled_template_sha256": hashlib.sha256(canonical_json_bytes(filled)).hexdigest(),
    }
    write_json_exclusive(args.output, filled)
    try:
        write_json_exclusive(args.evidence_output, evidence)
    except BaseException:
        args.output.unlink(missing_ok=True)
        raise
    return filled


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("apparatus", "confirmatory"):
        command = subparsers.add_parser(name)
        command.add_argument("--alias-smoke", type=Path, required=True)
        command.add_argument("--exact-smoke", type=Path, required=True)
        command.add_argument("--pricing", type=Path, required=True)
        command.add_argument("--template", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
    confirmatory = subparsers.choices["confirmatory"]
    confirmatory.add_argument("--claude-manifest", type=Path, required=True)
    confirmatory.add_argument("--claude-log", type=Path, required=True)
    confirmatory.add_argument("--claude-resources", type=Path, required=True)
    confirmatory.add_argument("--evidence-output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "apparatus":
            build_apparatus_manifest(
                args.template,
                args.output,
                args.alias_smoke,
                args.exact_smoke,
                args.pricing,
            )
        else:
            build_confirmatory_template(args)
    except (OSError, RuntimeError, SystemExit) as exc:
        print(f"runtime manifest preparation failed: {exc}")
        return 1
    print(f"prepared {args.command} manifest: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
