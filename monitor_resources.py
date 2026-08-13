#!/usr/bin/env python3
"""Record aggregate host and Docker resource peaks for an apparatus run.

The monitor never inspects a container filesystem, logs, environment, command,
or model output. It samples only Docker's aggregate CPU/memory counters and
host `/proc` counters while a separately launched runner PID is alive.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import tempfile
import time
from pathlib import Path


SIZE_MULTIPLIERS = {
    "B": 1,
    "kB": 1000,
    "KB": 1000,
    "MB": 1000**2,
    "GB": 1000**3,
    "TB": 1000**4,
    "KiB": 1024,
    "MiB": 1024**2,
    "GiB": 1024**3,
    "TiB": 1024**4,
}


def parse_size(value: str) -> int:
    compact = value.strip().replace(" ", "")
    for suffix in sorted(SIZE_MULTIPLIERS, key=len, reverse=True):
        if compact.endswith(suffix):
            number = float(compact[: -len(suffix)])
            if number < 0:
                raise ValueError("size must be non-negative")
            return int(round(number * SIZE_MULTIPLIERS[suffix]))
    raise ValueError(f"unsupported Docker size: {value!r}")


def parse_percent(value: str) -> float:
    compact = value.strip()
    if not compact.endswith("%"):
        raise ValueError(f"unsupported Docker percentage: {value!r}")
    number = float(compact[:-1])
    if number < 0:
        raise ValueError("percentage must be non-negative")
    return number


def parse_stats_lines(stdout: str, prefix: str) -> list[dict]:
    samples = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        name = row.get("Name")
        if not isinstance(name, str) or not name.startswith(prefix):
            continue
        usage = row.get("MemUsage", "").split("/", 1)[0]
        samples.append(
            {
                "memory_bytes": parse_size(usage),
                "cpu_percent": parse_percent(row.get("CPUPerc", "")),
            }
        )
    return samples


def docker_samples(prefix: str) -> list[dict]:
    completed = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError("docker stats failed")
    return parse_stats_lines(completed.stdout, prefix)


def read_meminfo(path: Path = Path("/proc/meminfo")) -> dict[str, int]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, raw = line.split(":", 1)
        fields = raw.split()
        if not fields:
            continue
        multiplier = 1024 if len(fields) > 1 and fields[1] == "kB" else 1
        values[key] = int(fields[0]) * multiplier
    required = ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree")
    if any(key not in values for key in required):
        raise RuntimeError("host meminfo omitted a required counter")
    return values


def host_sample() -> dict:
    memory = read_meminfo()
    return {
        "memory_total_bytes": memory["MemTotal"],
        "memory_available_bytes": memory["MemAvailable"],
        "swap_total_bytes": memory["SwapTotal"],
        "swap_used_bytes": memory["SwapTotal"] - memory["SwapFree"],
        "load_1m": os.getloadavg()[0],
    }


def process_alive(pid: int) -> bool:
    stat_path = Path(f"/proc/{pid}/stat")
    if stat_path.exists():
        try:
            # The command name is parenthesized and may itself contain spaces.
            state = stat_path.read_text(encoding="utf-8").rsplit(")", 1)[1].split()[0]
        except (OSError, IndexError):
            state = ""
        if state == "Z":
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Aggregate:
    def __init__(self) -> None:
        self.samples = 0
        self.docker_stats_failures = 0
        self.containers_observed = 0
        self.peak_concurrent_containers = 0
        self.peak_single_container_memory_bytes = 0
        self.peak_total_container_memory_bytes = 0
        self.peak_container_cpu_percent = 0.0
        self.memory_total_bytes = 0
        self.minimum_host_memory_available_bytes: int | None = None
        self.swap_total_bytes = 0
        self.peak_host_swap_used_bytes = 0
        self.peak_host_load_1m = 0.0

    def add(self, containers: list[dict], host: dict) -> None:
        self.samples += 1
        self.containers_observed += len(containers)
        self.peak_concurrent_containers = max(
            self.peak_concurrent_containers, len(containers)
        )
        if containers:
            self.peak_single_container_memory_bytes = max(
                self.peak_single_container_memory_bytes,
                max(item["memory_bytes"] for item in containers),
            )
            self.peak_total_container_memory_bytes = max(
                self.peak_total_container_memory_bytes,
                sum(item["memory_bytes"] for item in containers),
            )
            self.peak_container_cpu_percent = max(
                self.peak_container_cpu_percent,
                max(item["cpu_percent"] for item in containers),
            )
        self.memory_total_bytes = host["memory_total_bytes"]
        available = host["memory_available_bytes"]
        self.minimum_host_memory_available_bytes = (
            available
            if self.minimum_host_memory_available_bytes is None
            else min(self.minimum_host_memory_available_bytes, available)
        )
        self.swap_total_bytes = host["swap_total_bytes"]
        self.peak_host_swap_used_bytes = max(
            self.peak_host_swap_used_bytes, host["swap_used_bytes"]
        )
        self.peak_host_load_1m = max(self.peak_host_load_1m, host["load_1m"])

    def record(self, started_utc: str, finished_utc: str, elapsed_seconds: float) -> dict:
        return {
            "schema_version": 1,
            "status": "apparatus resource observation; not a research result",
            "started_utc": started_utc,
            "finished_utc": finished_utc,
            "elapsed_seconds": round(elapsed_seconds, 3),
            "architecture": platform.machine(),
            "cpu_count": os.cpu_count(),
            "samples": self.samples,
            "docker_stats_failures": self.docker_stats_failures,
            "container_observations": self.containers_observed,
            "peak_concurrent_containers": self.peak_concurrent_containers,
            "peak_single_container_memory_bytes": self.peak_single_container_memory_bytes,
            "peak_total_container_memory_bytes": self.peak_total_container_memory_bytes,
            "peak_container_cpu_percent": round(self.peak_container_cpu_percent, 3),
            "host_memory_total_bytes": self.memory_total_bytes,
            "minimum_host_memory_available_bytes": self.minimum_host_memory_available_bytes,
            "host_swap_total_bytes": self.swap_total_bytes,
            "peak_host_swap_used_bytes": self.peak_host_swap_used_bytes,
            "peak_host_load_1m": round(self.peak_host_load_1m, 3),
            "passed": self.containers_observed > 0,
        }


def atomic_write(path: Path, record: dict) -> None:
    if path.exists():
        raise RuntimeError(f"refusing to overwrite existing resource record: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def monitor(pid: int, prefix: str, poll_seconds: float) -> dict:
    if pid <= 0:
        raise ValueError("pid must be positive")
    if poll_seconds <= 0:
        raise ValueError("poll interval must be positive")
    aggregate = Aggregate()
    started = time.monotonic()
    started_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    while process_alive(pid):
        try:
            containers = docker_samples(prefix)
        except (RuntimeError, subprocess.TimeoutExpired, ValueError, json.JSONDecodeError):
            aggregate.docker_stats_failures += 1
            containers = []
        aggregate.add(containers, host_sample())
        time.sleep(poll_seconds)
    finished_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return aggregate.record(started_utc, finished_utc, time.monotonic() - started)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--container-prefix", default="loop-measurement-")
    args = parser.parse_args()
    record = monitor(args.pid, args.container_prefix, args.poll_seconds)
    if not record["passed"]:
        raise RuntimeError("resource monitor observed no measurement containers")
    atomic_write(args.output, record)
    print(f"passed={record['passed']}")
    print(f"samples={record['samples']}")
    print(f"peak_total_container_memory_bytes={record['peak_total_container_memory_bytes']}")
    print(f"output={args.output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as exc:
        print(f"resource monitoring failed: {exc}")
        raise SystemExit(1)
