#!/usr/bin/env python3
"""Bind a complete manifest template to the immutable preregistration tag."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

import run_measurement


FREEZE_SENTINEL = "__PREREGISTRATION_FREEZE_COMMIT__"


def git(repo: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout.strip()


def annotated_tag_target(repo: Path, tag: str) -> str:
    reference = f"refs/tags/{tag}"
    if git(repo, "cat-file", "-t", reference) != "tag":
        raise RuntimeError(f"{tag} must be an annotated tag")
    target = git(repo, "rev-list", "-n", "1", reference)
    if len(target) != 40:
        raise RuntimeError(f"{tag} did not resolve to a full commit ID")
    return target


def bind_freeze_commit(template: dict, freeze_commit: str) -> dict:
    if template.get("preregistration_commit") != FREEZE_SENTINEL:
        raise RuntimeError(
            "template preregistration_commit must be the freeze sentinel; "
            "a prefilled hash could silently bind the run to the wrong commit"
        )
    bound = dict(template)
    bound["preregistration_commit"] = freeze_commit
    return bound


def atomic_validated_write(path: Path, manifest: dict) -> None:
    if path.exists():
        raise RuntimeError(f"refusing to overwrite existing manifest: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        run_measurement.load_manifest(temporary_path)
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tag", default="prereg-v1")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()

    repo = args.repo.resolve()
    if git(repo, "status", "--porcelain"):
        raise RuntimeError("freeze binding requires a clean worktree")
    target = annotated_tag_target(repo, args.tag)
    if git(repo, "rev-parse", "HEAD") != target:
        raise RuntimeError("HEAD must still be the commit targeted by the freeze tag")

    template_path = args.template.resolve()
    try:
        relative_template = template_path.relative_to(repo)
    except ValueError as exc:
        raise RuntimeError("manifest template must be inside the frozen repository") from exc
    git(repo, "ls-files", "--error-unmatch", str(relative_template))

    template = json.loads(template_path.read_text(encoding="utf-8"))
    manifest = bind_freeze_commit(template, target)
    output_path = args.output.resolve()
    try:
        output_path.relative_to(repo)
    except ValueError as exc:
        raise RuntimeError("final manifest must be written inside the frozen repository") from exc
    atomic_validated_write(output_path, manifest)
    print(f"freeze_tag={args.tag}")
    print(f"preregistration_commit={target}")
    print(f"manifest_digest={run_measurement.manifest_digest(manifest)}")
    print(f"manifest={output_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, json.JSONDecodeError) as exc:
        print(f"manifest finalization failed: {exc}")
        raise SystemExit(1)
