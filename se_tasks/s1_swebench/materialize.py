#!/usr/bin/env python3
"""Materialize the pinned repository-scale S1 seed without hidden tests.

The Git repository is extracted from the official, digest-pinned SWE-bench
evaluation image at the registered base commit.  The image's object database
and every test-patch byte stay out of the agent workspace.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
CONFIG = json.loads((HERE / "instance.json").read_text(encoding="utf-8"))
CACHE = HERE / ".cache" / "seed"
MARKER = HERE / ".cache" / "seed.json"
LOCK = HERE / ".cache" / "materialize.lock"


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=True, text=True, **kwargs)


def image_available() -> bool:
    return subprocess.run(
        ["docker", "image", "inspect", CONFIG["evaluation_image"]],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def build_cache() -> None:
    expected = {
        "materializer_version": 3,
        "instance_id": CONFIG["instance_id"],
        "base_commit": CONFIG["base_commit"],
        "evaluation_image_id": CONFIG["evaluation_image_id"],
    }
    if (CACHE / "django" / "__init__.py").is_file() and MARKER.is_file():
        try:
            if json.loads(MARKER.read_text(encoding="utf-8")) == expected:
                return
        except (OSError, ValueError):
            pass

    if not image_available():
        run(["docker", "pull", CONFIG["evaluation_image"]])

    (HERE / ".cache").mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="loop-s1-seed-") as temp:
        output = Path(temp) / "seed"
        output.mkdir()
        command = (
            "set -eu; cd /testbed; "
            f"git reset --hard {CONFIG['base_commit']}; git clean -fdx; "
            "find . -mindepth 1 -maxdepth 1 ! -name .git "
            "-exec cp -rP --preserve=mode,timestamps {} /seed-out/ \\; ; "
            "test -f /seed-out/django/__init__.py; "
            f"chown -R {os.getuid()}:{os.getgid()} /seed-out"
        )
        run(
            [
                "docker", "run", "--rm", "--network", "none", "--cap-drop", "ALL",
                "--cap-add", "CHOWN", "--cap-add", "DAC_OVERRIDE",
                "--security-opt", "no-new-privileges", "--mount",
                f"type=bind,src={output},dst=/seed-out", CONFIG["evaluation_image"],
                "bash", "-lc", command,
            ]
        )
        if CACHE.exists():
            shutil.rmtree(CACHE)
        shutil.copytree(output, CACHE, symlinks=True)
    MARKER.write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def materialize(destination: Path) -> None:
    (HERE / ".cache").mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        build_cache()
    if destination.exists():
        raise SystemExit(f"destination already exists: {destination}")
    shutil.copytree(CACHE, destination, symlinks=True)
    issue = (
        "# Repository defect repair\n\n"
        "Fix SWE-bench Verified instance `django__django-16938` in this pinned "
        "Django repository.\n\n"
        "Serialization of a many-to-many relation fails when its custom manager "
        "uses `select_related()`. The serializer narrows the related queryset to "
        "only the primary key, which conflicts with the manager's traversal. "
        "Repair the production implementation without changing any tests or test "
        "infrastructure. Preserve all existing serializer formats and behavior.\n"
    )
    (destination / "ISSUE.md").write_text(issue, encoding="utf-8")
    public = (
        "# Public verification\n\n"
        "The repository's existing tests are public. Hidden issue-specific tests "
        "are added only inside the network-disabled oracle container. You may run "
        "syntax checks or any existing public test. Do not edit `tests/` or "
        "`django/test/`; such a candidate is invalid.\n"
    )
    (destination / "PUBLIC_TESTS.md").write_text(public, encoding="utf-8")
    task_record = {
        "schema_version": 1,
        "instance_id": CONFIG["instance_id"],
        "base_commit": CONFIG["base_commit"],
        "hidden_tests_present": False,
    }
    (destination / ".loop-task.json").write_text(
        json.dumps(task_record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    materialize(args.destination.resolve())


if __name__ == "__main__":
    main()
