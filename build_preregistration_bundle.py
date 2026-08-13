#!/usr/bin/env python3
"""Build the immutable pre-outcome bundle for external timestamping."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import audit_public_history
import build_replication_bundle
import finalize_measurement_manifest
import replay
import run_measurement


ROOT = Path(__file__).resolve().parent
PREREG_TAG = "prereg-v1"
ZENODO_METADATA_PATH = ROOT / ".zenodo-prereg.json"
ZENODO_METADATA = json.loads(ZENODO_METADATA_PATH.read_text(encoding="utf-8"))


def fail(message: str) -> None:
    raise RuntimeError(message)


def file_sha256(path: Path) -> str:
    return replay.file_sha256(path)


def git(*arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(ROOT), *arguments],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        fail(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout.strip()


def json_object(path: Path) -> tuple[dict, bytes]:
    payload = path.read_bytes()
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid JSON object: {path}") from exc
    if not isinstance(value, dict):
        fail(f"JSON root must be an object: {path}")
    return value, payload


def tagged_relative(path: Path, tag: str) -> str:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(ROOT).as_posix()
    except ValueError as exc:
        raise RuntimeError(f"bundle input is outside the frozen repository: {path}") from exc
    git("cat-file", "-e", f"{tag}:{relative}")
    if git("rev-parse", f"{tag}:{relative}") != git("hash-object", str(resolved)):
        fail(f"bundle input differs from the preregistration tag: {relative}")
    return relative


def validate_runtime_hashes(
    evidence: dict,
    runtime_template_path: Path,
    inputs: dict[str, Path],
) -> None:
    if evidence.get("schema_version") != 1:
        fail("runtime preparation evidence must use schema 1")
    expected = {
        "alias_smoke_sha256": inputs["alias-smoke"],
        "exact_smoke_sha256": inputs["exact-smoke"],
        "pricing_record_sha256": inputs["pricing"],
        "claude_apparatus_manifest_sha256": inputs["claude-manifest"],
        "claude_apparatus_log_sha256": inputs["claude-log"],
        "claude_apparatus_resources_sha256": inputs["claude-resources"],
    }
    mismatches = [
        field
        for field, path in expected.items()
        if evidence.get(field) != file_sha256(path)
    ]
    if evidence.get("filled_template_sha256") != file_sha256(runtime_template_path):
        mismatches.append("filled_template_sha256")
    if mismatches:
        fail("runtime preparation evidence has mismatched digests: " + ", ".join(mismatches))


def public_history_evidence(tag_target: str) -> bytes:
    findings, counts = audit_public_history.scan(ROOT)
    unexpected = audit_public_history.unexpected_findings(findings)
    if unexpected:
        fail("public history audit did not pass for the preregistration bundle")
    report = {
        "schema_version": 1,
        "status": "public-history-audit-passed",
        "audited_commit": tag_target,
        **counts,
        "allowed_historical_findings": [
            {
                "object_id": object_id,
                "path": path,
                "pattern": pattern,
                "count": count,
            }
            for object_id, path, pattern, count in sorted(
                audit_public_history.ALLOWED_HISTORICAL_FINDINGS
            )
        ],
        "unexpected_findings": 0,
    }
    return (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()


def validate(args: argparse.Namespace) -> dict:
    tag_target = finalize_measurement_manifest.annotated_tag_target(ROOT, PREREG_TAG)
    if git("rev-parse", "HEAD") != tag_target:
        fail("HEAD must remain at the prereg-v1 target while timestamping")
    if git("status", "--porcelain", "--untracked-files=no"):
        fail("tracked repository files must be clean while timestamping")

    tracked_inputs = {
        "runtime-template": args.runtime_template,
        "runtime-evidence": args.runtime_evidence,
        "alias-smoke": args.alias_smoke,
        "exact-smoke": args.exact_smoke,
        "pricing": args.pricing,
        "claude-manifest": args.claude_manifest,
        "claude-log": args.claude_log,
        "claude-resources": args.claude_resources,
    }
    relative_paths = {
        name: tagged_relative(path, PREREG_TAG)
        for name, path in tracked_inputs.items()
    }
    tagged_relative(ZENODO_METADATA_PATH, PREREG_TAG)

    template, template_payload = json_object(args.runtime_template)
    if finalize_measurement_manifest.unresolved_value_sentinels(template):
        fail("runtime template still has unresolved value placeholders")
    expected_manifest = finalize_measurement_manifest.bind_freeze_commit(
        template, tag_target
    )
    manifest, manifest_payload = json_object(args.manifest)
    if manifest != expected_manifest:
        fail("measurement manifest is not the exact tagged runtime template binding")
    if run_measurement.load_manifest(args.manifest) != manifest:
        fail("measurement manifest failed validation")

    evidence, evidence_payload = json_object(args.runtime_evidence)
    validate_runtime_hashes(
        evidence,
        args.runtime_template,
        {name: path for name, path in tracked_inputs.items() if name != "runtime-template"},
    )
    claude_agents = [agent for agent in manifest["agents"] if agent["name"] == "claude"]
    if (
        len(claude_agents) != 1
        or claude_agents[0]["model"] != evidence.get("exact_claude_model")
        or manifest["estimated_api_equivalent_usd_per_trajectory"]
        != evidence.get("estimated_api_equivalent_usd_per_trajectory")
    ):
        fail("runtime evidence does not match the frozen measurement manifest")
    preflight_path = args.manifest.parent / manifest["isolation_preflight_record"]
    if file_sha256(preflight_path) != manifest["isolation_preflight_sha256"]:
        fail("frozen isolation preflight digest does not match the manifest")
    preflight_relative = tagged_relative(preflight_path, PREREG_TAG)

    snapshots = {
        "measurement-manifest.json": manifest_payload,
        "measurement-manifest.runtime.template.json": template_payload,
        "runtime-shadow-budget-evidence.json": evidence_payload,
        "isolation-preflight.json": preflight_path.read_bytes(),
        "public-history-audit.json": public_history_evidence(tag_target),
    }
    for name, path in tracked_inputs.items():
        if name in {"runtime-template", "runtime-evidence"}:
            continue
        snapshots[path.name] = path.read_bytes()
    return {
        "tag_target": tag_target,
        "manifest_digest": run_measurement.manifest_digest(manifest),
        "relative_paths": {**relative_paths, "preflight": preflight_relative},
        "snapshots": snapshots,
    }


def build(args: argparse.Namespace) -> Path:
    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        fail("output directory already exists")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    validated = validate(args)
    stage = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        payload = stage / "payload"
        payload.mkdir()
        for name, content in validated["snapshots"].items():
            (payload / name).write_bytes(content)
        source_archive = payload / "loop-engineering-source-prereg-v1.tar.gz"
        build_replication_bundle.write_source_archive(source_archive)
        metadata_payload = (
            json.dumps(ZENODO_METADATA, indent=2, sort_keys=True) + "\n"
        ).encode()
        (payload / "zenodo-metadata.json").write_bytes(metadata_payload)
        bundle_manifest = {
            "schema_version": 1,
            "status": "pre-outcome preregistration timestamp bundle",
            "preregistration_tag": PREREG_TAG,
            "preregistration_commit": validated["tag_target"],
            "measurement_manifest_digest": validated["manifest_digest"],
            "tagged_input_paths": validated["relative_paths"],
            "files": {
                path.name: {"bytes": path.stat().st_size, "sha256": file_sha256(path)}
                for path in sorted(payload.iterdir())
            },
        }
        manifest_path = payload / "preregistration-bundle-manifest.json"
        manifest_path.write_text(
            json.dumps(bundle_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        checksummed = sorted(payload.iterdir(), key=lambda path: path.name)
        (payload / "SHA256SUMS").write_text(
            "".join(f"{file_sha256(path)}  {path.name}\n" for path in checksummed),
            encoding="utf-8",
        )
        bundle = stage / "loop-engineering-preregistration-v1.zip"
        build_replication_bundle.deterministic_zip(
            bundle,
            list(payload.iterdir()),
            prefix="loop-engineering-preregistration-v1",
        )
        shutil.rmtree(payload)
        (stage / "loop-engineering-preregistration-v1.zip.sha256").write_text(
            f"{file_sha256(bundle)}  {bundle.name}\n",
            encoding="utf-8",
        )
        (stage / "zenodo-deposition-metadata.json").write_bytes(metadata_payload)
        os.replace(stage, output_dir)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return output_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--runtime-template", type=Path, required=True)
    parser.add_argument("--runtime-evidence", type=Path, required=True)
    parser.add_argument("--alias-smoke", type=Path, required=True)
    parser.add_argument("--exact-smoke", type=Path, required=True)
    parser.add_argument("--pricing", type=Path, required=True)
    parser.add_argument("--claude-manifest", type=Path, required=True)
    parser.add_argument("--claude-log", type=Path, required=True)
    parser.add_argument("--claude-resources", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        output = build(args)
    except (OSError, RuntimeError, SystemExit) as exc:
        print(f"preregistration bundle failed: {exc}")
        return 1
    print(f"built preregistration bundle: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
