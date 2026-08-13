#!/usr/bin/env python3
"""Build a fail-closed, digest-bound confirmatory replication bundle."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

import replay
import run_measurement
import zenodo_preregistration


ROOT = Path(__file__).resolve().parent
PREREG_TAG = "prereg-v1"
EXPECTED_TRAJECTORIES = 160
EXPECTED_LOGICAL_ROWS = 960
ZENODO_METADATA = json.loads((ROOT / ".zenodo.json").read_text(encoding="utf-8"))


def fail(message: str) -> None:
    raise RuntimeError(message)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: Path) -> str:
    return replay.file_sha256(path)


def json_snapshot(path: Path) -> tuple[dict, bytes]:
    payload = path.read_bytes()
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        fail(f"JSON root must be an object: {path}")
    return value, payload


def git_output(*args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        fail(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def validate_inputs(
    manifest_path: Path,
    log_path: Path,
    replay_path: Path,
    audit_path: Path,
    analysis_path: Path,
    archive_root: Path,
    preregistration_evidence_path: Path,
    preregistration_bundle_path: Path,
) -> dict:
    source_log_sha256 = file_sha256(log_path)
    manifest_file_sha256 = file_sha256(manifest_path)
    manifest = run_measurement.load_manifest(manifest_path)
    if file_sha256(manifest_path) != manifest_file_sha256:
        fail("measurement manifest changed during release validation")
    if manifest.get("apparatus_test") is not False:
        fail("replication bundle requires a confirmatory manifest")
    external_publication = zenodo_preregistration.validate_publication_evidence(
        preregistration_evidence_path,
        preregistration_bundle_path,
        manifest,
    )
    configured_archive = (manifest_path.parent / manifest["artifact_archive_dir"]).resolve()
    if configured_archive != archive_root.resolve():
        fail("archive root does not match the frozen measurement manifest")

    cycles, abandoned, unparsable = replay.load(log_path)
    integrity = replay.integrity(
        cycles,
        abandoned,
        unparsable,
        archive_root=archive_root,
        require_archive_files=True,
    )
    integrity["source_log_stable"] = file_sha256(log_path) == source_log_sha256
    integrity["clean"] = bool(integrity["clean"] and integrity["source_log_stable"])
    if not integrity["clean"]:
        fail(f"raw log or candidate archive integrity is not clean: {integrity}")
    grouped = replay.group_trajectories(cycles, abandoned)
    if len(grouped) != EXPECTED_TRAJECTORIES:
        fail(f"expected {EXPECTED_TRAJECTORIES} completed trajectories")
    if sum(len(rows) for rows in grouped.values()) != EXPECTED_LOGICAL_ROWS:
        fail(f"expected {EXPECTED_LOGICAL_ROWS} completed logical cycle rows")

    manifest_digest = run_measurement.manifest_digest(manifest)
    preregistration_commit = manifest["preregistration_commit"]
    for record in [*cycles, *abandoned]:
        if record.get("manifest_digest") != manifest_digest:
            fail("a raw-log row has a different measurement manifest digest")
        if record.get("preregistration_commit") != preregistration_commit:
            fail("a raw-log row has a different preregistration commit")
        if record.get("schema_version") != run_measurement.SCHEMA_VERSION:
            fail("a raw-log row does not use the current provenance schema")
        if any(record.get(field) != value for field, value in external_publication.items()):
            fail("a raw-log row has different external preregistration evidence")

    tag_commit = git_output("rev-parse", f"{PREREG_TAG}^{{commit}}")
    if tag_commit != preregistration_commit:
        fail("prereg-v1 tag does not match the frozen manifest commit")
    if git_output("status", "--porcelain", "--untracked-files=no"):
        fail("tracked repository files must be clean before release packaging")
    postfreeze_changes = {
        name
        for name in git_output("diff", "--name-only", f"{PREREG_TAG}..HEAD").splitlines()
        if name
    }
    if postfreeze_changes - {"measurement-manifest.json"}:
        fail("tracked code or metadata changed after the preregistration tag")

    preflight_path = manifest_path.parent / manifest["isolation_preflight_record"]
    preflight_payload = preflight_path.read_bytes()
    if sha256_bytes(preflight_payload) != manifest["isolation_preflight_sha256"]:
        fail("isolation preflight does not match the frozen manifest")

    replay_report, replay_payload = json_snapshot(replay_path)
    audit_report, audit_payload = json_snapshot(audit_path)
    analysis_report, analysis_payload = json_snapshot(analysis_path)
    if replay_report.get("schema_version") != 3:
        fail("standalone replay must use schema 3")
    if replay_report.get("source_log_sha256") != source_log_sha256:
        fail("standalone replay has a different source-log digest")
    if replay_report.get("integrity") != integrity:
        fail("standalone replay integrity does not reproduce the release audit")
    if audit_report.get("schema_version") != 2 or audit_report.get("clean") is not True:
        fail("standalone reward-hacking audit is not clean schema 2")
    if (
        audit_report.get("source_log_sha256") != source_log_sha256
        or audit_report.get("source_log_stable") is not True
    ):
        fail("standalone reward-hacking audit has a different or unstable log")
    if analysis_report.get("schema_version") != 3:
        fail("confirmatory analysis must use schema 3")
    if analysis_report.get("source_log_sha256") != source_log_sha256:
        fail("confirmatory analysis has a different source-log digest")
    if analysis_report.get("integrity") != integrity:
        fail("confirmatory analysis integrity does not reproduce the release audit")
    if analysis_report.get("reward_hacking_audit") != audit_report:
        fail("confirmatory analysis does not embed the standalone reward-hacking audit")
    if (
        analysis_report.get("trajectories") != EXPECTED_TRAJECTORIES
        or analysis_report.get("blocks") != 40
    ):
        fail("confirmatory analysis does not contain 160 trajectories and 40 blocks")
    if file_sha256(log_path) != source_log_sha256:
        fail("raw log changed during release validation")
    manifest_payload = manifest_path.read_bytes()
    if sha256_bytes(manifest_payload) != manifest_file_sha256:
        fail("measurement manifest changed during release validation")
    log_payload = log_path.read_bytes()
    if sha256_bytes(log_payload) != source_log_sha256:
        fail("raw log changed while its release snapshot was read")
    preregistration_evidence_payload = preregistration_evidence_path.read_bytes()
    if (
        sha256_bytes(preregistration_evidence_payload)
        != external_publication["external_preregistration_evidence_sha256"]
    ):
        fail("external preregistration evidence changed during release validation")

    return {
        "manifest": manifest,
        "manifest_digest": manifest_digest,
        "preregistration_commit": preregistration_commit,
        "source_log_sha256": source_log_sha256,
        "external_publication": external_publication,
        "preflight_path": preflight_path,
        "cycles": cycles,
        "snapshots": {
            "measurement-manifest.json": manifest_payload,
            "isolation-preflight.json": preflight_payload,
            "confirmatory-cycles.jsonl": log_payload,
            "confirmatory-replay.json": replay_payload,
            "confirmatory-reward-hacking.json": audit_payload,
            "confirmatory-analysis.json": analysis_payload,
            "external-preregistration-publication.json": (
                preregistration_evidence_payload
            ),
        },
    }


def archive_file_map(cycles: list[dict], archive_root: Path) -> dict[str, Path]:
    manifest_ids = sorted(
        {
            row["candidate_archive_manifest_sha256"]
            for row in cycles
            if row.get("schema_version", 0) >= 3 and not row.get("apparatus_test")
        }
    )
    files: dict[str, Path] = {}
    object_ids: set[str] = set()
    for manifest_id in manifest_ids:
        error = replay.verify_candidate_archive(archive_root, manifest_id)
        if error:
            fail(f"candidate archive {manifest_id} is invalid: {error}")
        path = archive_root / "manifests" / f"{manifest_id}.json"
        files[f"candidate-archive/manifests/{manifest_id}.json"] = path
        record = json.loads(path.read_bytes()[:-1])
        object_ids.update(
            entry["sha256"]
            for entry in record["entries"]
            if entry["type"] == "file"
        )
    for object_id in sorted(object_ids):
        files[f"candidate-archive/objects/{object_id[:2]}/{object_id}"] = (
            archive_root / "objects" / object_id[:2] / object_id
        )
    return files


def deterministic_tar_gz(output: Path, files: dict[str, Path]) -> None:
    with output.open("wb") as destination:
        with gzip.GzipFile(filename="", mode="wb", fileobj=destination, mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode="w|") as archive:
                for name, path in sorted(files.items()):
                    size = path.stat().st_size
                    info = tarfile.TarInfo(name)
                    info.size = size
                    info.mode = 0o444
                    info.mtime = 0
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    with path.open("rb") as source:
                        archive.addfile(info, source)


def verify_candidate_tar(path: Path, expected_names: set[str]) -> None:
    with tarfile.open(path, mode="r:gz") as archive:
        members = archive.getmembers()
        if {member.name for member in members} != expected_names:
            fail("candidate archive tar has the wrong member set")
        for member in members:
            if not member.isfile() or member.issym() or member.islnk():
                fail("candidate archive tar contains a non-regular member")
            extracted = archive.extractfile(member)
            if extracted is None:
                fail("candidate archive tar member is unreadable")
            digest = hashlib.sha256()
            size = 0
            for chunk in iter(lambda: extracted.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
            if size != member.size:
                fail("candidate archive tar member size mismatch")
            name = Path(member.name).name
            if "/objects/" in member.name and digest.hexdigest() != name:
                fail("candidate archive tar object digest mismatch")
            if "/manifests/" in member.name:
                payload = b""
                with archive.extractfile(member) as handle:
                    payload = handle.read()
                if not payload.endswith(b"\n") or sha256_bytes(payload[:-1]) != name[:-5]:
                    fail("candidate archive tar manifest digest mismatch")


def deterministic_zip(
    output: Path,
    files: list[Path],
    prefix: str = "loop-engineering-confirmatory-v1",
) -> None:
    with zipfile.ZipFile(
        output, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in sorted(files, key=lambda item: item.name):
            info = zipfile.ZipInfo(
                f"{prefix}/{path.name}",
                date_time=(1980, 1, 1, 0, 0, 0),
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100444 << 16
            info.create_system = 3
            with path.open("rb") as source, archive.open(info, mode="w") as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)


def write_source_archive(output: Path) -> None:
    temporary_tar = output.with_suffix("")
    with temporary_tar.open("wb") as destination:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(ROOT),
                "archive",
                "--format=tar",
                "--prefix=loop-engineering-lab-prereg-v1/",
                PREREG_TAG,
            ],
            stdout=destination,
            stderr=subprocess.PIPE,
            check=False,
        )
    if completed.returncode:
        temporary_tar.unlink(missing_ok=True)
        fail(f"git archive failed: {completed.stderr.decode(errors='replace').strip()}")
    with temporary_tar.open("rb") as source, output.open("wb") as destination:
        with gzip.GzipFile(filename="", mode="wb", fileobj=destination, mtime=0) as zipped:
            shutil.copyfileobj(source, zipped, length=1024 * 1024)
    temporary_tar.unlink()


def build(args: argparse.Namespace) -> Path:
    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        fail("output directory already exists")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    validated = validate_inputs(
        args.manifest.resolve(),
        args.log.resolve(),
        args.replay.resolve(),
        args.reward_audit.resolve(),
        args.analysis.resolve(),
        args.archive_root.resolve(),
        args.preregistration_evidence.resolve(),
        args.preregistration_bundle.resolve(),
    )
    stage = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        payload_dir = stage / "payload"
        payload_dir.mkdir()
        for name, payload in validated["snapshots"].items():
            (payload_dir / name).write_bytes(payload)
        source_archive = payload_dir / "loop-engineering-source-prereg-v1.tar.gz"
        write_source_archive(source_archive)
        candidate_files = archive_file_map(validated["cycles"], args.archive_root.resolve())
        candidate_archive = payload_dir / "candidate-archive.tar.gz"
        deterministic_tar_gz(candidate_archive, candidate_files)
        verify_candidate_tar(candidate_archive, set(candidate_files))
        metadata_payload = json.dumps(ZENODO_METADATA, indent=2, sort_keys=True) + "\n"
        (payload_dir / "zenodo-metadata.json").write_text(
            metadata_payload,
            encoding="utf-8",
        )

        payload_files = sorted(payload_dir.iterdir())
        release_manifest = {
            "schema_version": 1,
            "preregistration_tag": PREREG_TAG,
            "preregistration_commit": validated["preregistration_commit"],
            "measurement_manifest_digest": validated["manifest_digest"],
            "source_log_sha256": validated["source_log_sha256"],
            **validated["external_publication"],
            "completed_trajectories": EXPECTED_TRAJECTORIES,
            "completed_logical_cycle_rows": EXPECTED_LOGICAL_ROWS,
            "candidate_archive_members": len(candidate_files),
            "files": {
                path.name: {"bytes": path.stat().st_size, "sha256": file_sha256(path)}
                for path in payload_files
            },
        }
        manifest_path = payload_dir / "replication-manifest.json"
        manifest_path.write_text(
            json.dumps(release_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        checksummed = sorted([*payload_files, manifest_path], key=lambda path: path.name)
        (payload_dir / "SHA256SUMS").write_text(
            "".join(f"{file_sha256(path)}  {path.name}\n" for path in checksummed),
            encoding="utf-8",
        )
        bundle_path = stage / "loop-engineering-confirmatory-v1.zip"
        deterministic_zip(bundle_path, list(payload_dir.iterdir()))
        (stage / "zenodo-deposition-metadata.json").write_text(
            metadata_payload,
            encoding="utf-8",
        )
        (stage / "loop-engineering-confirmatory-v1.zip.sha256").write_text(
            f"{file_sha256(bundle_path)}  {bundle_path.name}\n",
            encoding="utf-8",
        )
        shutil.rmtree(payload_dir)
        if file_sha256(args.log.resolve()) != validated["source_log_sha256"]:
            fail("raw log changed while the replication bundle was being built")
        if (
            file_sha256(args.preregistration_bundle.resolve())
            != validated["external_publication"][
                "external_preregistration_bundle_sha256"
            ]
        ):
            fail("external preregistration ZIP changed during release packaging")
        os.replace(stage, output_dir)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return output_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--reward-audit", type=Path, required=True)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--preregistration-evidence", type=Path, required=True)
    parser.add_argument("--preregistration-bundle", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        output = build(args)
    except (OSError, RuntimeError, SystemExit) as exc:
        print(f"replication bundle failed: {exc}")
        return 1
    print(f"built replication bundle: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
