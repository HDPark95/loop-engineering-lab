#!/usr/bin/env python3
"""Fail closed on new sensitive material in the public reachable history."""

from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


FORBIDDEN_TEXT = {
    "organization-1": b"async" + b"site",
    "organization-2": b"team" + b"grit",
    "product-1": b"gyup" + b"gyup",
    "product-2-ko": ("그" + "릿").encode(),
    "product-3-ko": ("겹" + "겹").encode(),
    "organization-3": b"for" + b"biz",
    "organization-4": b"nex" + b"on",
}
LOCAL_PATH = re.compile(
    rb"/Users/[^/\s]+/" + b"async" + b"site", re.IGNORECASE
)
CREDENTIAL_PATTERNS = {
    "slack-token": re.compile(rb"xox[baprs]-[A-Za-z0-9-]{20,}"),
    "anthropic-key": re.compile(rb"sk-ant-[A-Za-z0-9_-]{20,}"),
    "openai-key": re.compile(rb"sk-(?!ant-)(?:proj-)?[A-Za-z0-9_-]{20,}"),
    "github-token": re.compile(
        rb"(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}"
    ),
    "aws-access-key": re.compile(rb"AKIA[0-9A-Z]{16}"),
    "private-key": re.compile(
        rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    ),
    "oauth-json-secret": re.compile(
        rb'"(?:accessToken|refreshToken)"\s*:\s*"[^"\r\n]{40,}"'
    ),
}
ALLOWED_HISTORICAL_FINDINGS = {
    (
        "dbe3eff0b0a67093474d89918cb4a6116b1baacb",
        "se_tasks/s1_swebench/instance.json",
        "organization-1",
        1,
    ),
    (
        "2c92cc8763696acd1cb1bcc5a5d3f1224b1ce943",
        "se_tasks/s1_swebench/instance.json",
        "organization-1",
        1,
    ),
}


@dataclass(frozen=True, order=True)
class Finding:
    scope: str
    object_id: str
    path: str
    pattern: str
    count: int


def git(repo: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        detail = completed.stderr or completed.stdout
        raise RuntimeError(detail.decode(errors="replace").strip())
    return completed.stdout


def matches(data: bytes) -> list[tuple[str, int]]:
    lowered = data.lower()
    found = [
        (name, lowered.count(token.lower()))
        for name, token in FORBIDDEN_TEXT.items()
        if token.lower() in lowered
    ]
    local_paths = len(LOCAL_PATH.findall(data))
    if local_paths:
        found.append(("local-user-path", local_paths))
    for name, pattern in CREDENTIAL_PATTERNS.items():
        count = len(pattern.findall(data))
        if count:
            found.append((name, count))
    return found


def tree_entries(repo: Path, treeish: str) -> dict[tuple[str, str], bytes]:
    entries: dict[tuple[str, str], bytes] = {}
    output = git(repo, "ls-tree", "-rz", treeish)
    for record in output.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        _mode, kind, raw_object = metadata.split(b" ", 2)
        if kind != b"blob":
            continue
        object_id = raw_object.decode("ascii")
        path = raw_path.decode("utf-8", errors="surrogateescape")
        entries[(object_id, path)] = git(repo, "cat-file", "blob", object_id)
    return entries


def scan(repo: Path) -> tuple[list[Finding], dict[str, int]]:
    commits = git(repo, "rev-list", "HEAD").decode().splitlines()
    if not commits:
        raise RuntimeError("public history audit requires at least one commit")
    head_entries = tree_entries(repo, "HEAD")
    historical_entries: dict[tuple[str, str], bytes] = {}
    for commit in commits:
        historical_entries.update(tree_entries(repo, commit))

    findings: list[Finding] = []
    for (object_id, path), data in head_entries.items():
        findings.extend(
            Finding("current-tree", object_id, path, pattern, count)
            for pattern, count in matches(data)
        )
    for (object_id, path), data in historical_entries.items():
        findings.extend(
            Finding("reachable-blob", object_id, path, pattern, count)
            for pattern, count in matches(data)
        )

    metadata = git(
        repo,
        "log",
        "--format=%H%x00%an%x00%ae%x00%cn%x00%ce%x00%B%x00",
        "HEAD",
    )
    findings.extend(
        Finding("commit-metadata", "HEAD", "", pattern, count)
        for pattern, count in matches(metadata)
    )
    return sorted(set(findings)), {
        "reachable_commits": len(commits),
        "current_tree_blobs": len(head_entries),
        "unique_reachable_blob_paths": len(historical_entries),
    }


def unexpected_findings(findings: list[Finding]) -> list[Finding]:
    unexpected = []
    observed_allowed = set()
    for finding in findings:
        key = (finding.object_id, finding.path, finding.pattern, finding.count)
        if finding.scope == "reachable-blob" and key in ALLOWED_HISTORICAL_FINDINGS:
            observed_allowed.add(key)
        else:
            unexpected.append(finding)
    missing = ALLOWED_HISTORICAL_FINDINGS - observed_allowed
    unexpected.extend(
        Finding("missing-allowlisted-history", blob, path, pattern, count)
        for blob, path, pattern, count in sorted(missing)
    )
    return sorted(unexpected)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    repo = args.repo.resolve()
    if git(repo, "status", "--porcelain"):
        raise RuntimeError("public history audit requires a clean worktree")
    findings, counts = scan(repo)
    unexpected = unexpected_findings(findings)
    if unexpected:
        detail = "; ".join(
            f"{item.scope}:{item.pattern}:{item.object_id}:{item.path}:{item.count}"
            for item in unexpected
        )
        raise RuntimeError("public history audit found an unexpected item: " + detail)
    print("public_history_audit=passed")
    for key, value in counts.items():
        print(f"{key}={value}")
    print(f"allowed_historical_findings={len(ALLOWED_HISTORICAL_FINDINGS)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"public history audit failed: {exc}")
        raise SystemExit(1)
