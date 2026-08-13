#!/usr/bin/env python3
"""Generate fail-closed evidence for the frozen candidate sandbox image."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import tempfile
import time
from pathlib import Path

import run_measurement
from se_tasks._sandbox import harness


PROBE_SOURCE = r'''
import os
import socket
from pathlib import Path


def handle(request):
    source_candidates = (
        "/oracle/se_tasks",
        "/workspace/se_tasks",
        "/app/se_tasks",
        "/repo/se_tasks",
        "/oracle/oracle.py",
    )
    readable = [path for path in source_candidates if Path(path).exists()]
    network_connected = False
    network_error = None
    try:
        with socket.create_connection(("1.1.1.1", 53), timeout=1.0):
            network_connected = True
    except Exception as exc:
        network_error = type(exc).__name__
    rootfs_write_succeeded = False
    rootfs_write_error = None
    try:
        Path("/loop-sandbox-write-probe").write_text("should fail")
        rootfs_write_succeeded = True
    except Exception as exc:
        rootfs_write_error = type(exc).__name__
    status = Path("/proc/self/status").read_text()
    cap_eff = next(
        line.split(":", 1)[1].strip()
        for line in status.splitlines()
        if line.startswith("CapEff:")
    )
    root_mount = next(
        line for line in Path("/proc/mounts").read_text().splitlines()
        if line.split()[1] == "/"
    )
    root_mount_options = root_mount.split()[3].split(",")
    return {
        "readable_oracle_paths": readable,
        "network_connected": network_connected,
        "network_error": network_error,
        "rootfs_write_succeeded": rootfs_write_succeeded,
        "rootfs_write_error": rootfs_write_error,
        "root_mount_read_only": "ro" in root_mount_options,
        "network_interfaces": sorted(path.name for path in Path("/sys/class/net").iterdir()),
        "effective_uid": os.geteuid(),
        "effective_capabilities_hex": cap_eff,
    }
'''


def validate_probe(probe: dict) -> list[str]:
    failures = []
    if probe.get("readable_oracle_paths") != []:
        failures.append("held-out source is readable")
    if probe.get("network_connected") is not False:
        failures.append("network connection succeeded")
    if probe.get("rootfs_write_succeeded") is not False:
        failures.append("container root filesystem was writable")
    if probe.get("root_mount_read_only") is not True:
        failures.append("container root mount was not read-only")
    if probe.get("network_interfaces") != ["lo"]:
        failures.append("candidate had a non-loopback network interface")
    if probe.get("effective_uid") != 65534:
        failures.append("candidate did not run as uid 65534")
    if probe.get("effective_capabilities_hex") != "0000000000000000":
        failures.append("candidate retained Linux capabilities")
    return failures


def docker_output(*arguments: str) -> str:
    completed = subprocess.run(
        ["docker", *arguments], capture_output=True, text=True, check=False, timeout=30
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout.strip()


def host_record() -> dict:
    memory_bytes = None
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        first = meminfo.read_text(encoding="utf-8").splitlines()[0].split()
        if len(first) >= 2 and first[0] == "MemTotal:":
            memory_bytes = int(first[1]) * 1024
    return {
        "architecture": platform.machine(),
        "cpu_count": os.cpu_count(),
        "memory_bytes": memory_bytes,
        "docker_server_version": docker_output("version", "--format", "{{.Server.Version}}"),
    }


def generate(image: str) -> dict:
    if not run_measurement.image_is_digest_pinned(image):
        raise RuntimeError("sandbox image must be pinned by sha256 digest")
    resolved = docker_output("image", "inspect", image, "--format", "{{.Id}}")
    if resolved != image:
        raise RuntimeError(f"sandbox image resolved to {resolved}, expected {image}")
    with tempfile.TemporaryDirectory(prefix="loop-isolation-preflight-") as tmp:
        candidate = Path(tmp)
        (candidate / "probe.py").write_text(PROBE_SOURCE, encoding="utf-8")
        previous = os.environ.get("LOOP_SANDBOX_IMAGE")
        os.environ["LOOP_SANDBOX_IMAGE"] = image
        try:
            outcome = harness.run_calls(candidate, "probe", "handle", [{}])
        finally:
            if previous is None:
                os.environ.pop("LOOP_SANDBOX_IMAGE", None)
            else:
                os.environ["LOOP_SANDBOX_IMAGE"] = previous
    if not outcome.get("ok") or not outcome.get("results"):
        raise RuntimeError(f"sandbox probe did not return a result: {outcome}")
    probe = outcome["results"][0].get("value") or {}
    failures = validate_probe(probe)
    return {
        "schema_version": 1,
        "status": "candidate sandbox isolation preflight; not a research result",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sandbox_image_requested": image,
        "sandbox_image_resolved": resolved,
        "host": host_record(),
        "probe": probe,
        "failures": failures,
        "passed": not failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sandbox-image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError(f"refusing to overwrite existing preflight: {args.output}")
    record = generate(args.sandbox_image)
    if not record["passed"]:
        raise RuntimeError("sandbox isolation preflight failed: " + ", ".join(record["failures"]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"sandbox_image={record['sandbox_image_resolved']}")
    print(f"passed={record['passed']}")
    print(f"output={args.output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"isolation preflight failed: {exc}")
        raise SystemExit(1)
