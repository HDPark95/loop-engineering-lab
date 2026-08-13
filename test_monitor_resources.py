"""Tests for aggregate-only apparatus resource monitoring."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import monitor_resources


class ResourceMonitorTest(unittest.TestCase):
    def test_docker_sizes_and_percentages_are_parsed(self):
        self.assertEqual(monitor_resources.parse_size("1.5MiB"), 1572864)
        self.assertEqual(monitor_resources.parse_size("2 GB"), 2000000000)
        self.assertEqual(monitor_resources.parse_percent("125.5%"), 125.5)
        with self.assertRaises(ValueError):
            monitor_resources.parse_size("unknown")

    def test_only_measurement_containers_are_aggregated(self):
        rows = "\n".join(
            [
                '{"Name":"loop-measurement-claude-a","MemUsage":"10MiB / 1GiB","CPUPerc":"20%"}',
                '{"Name":"unrelated","MemUsage":"4GiB / 8GiB","CPUPerc":"99%"}',
            ]
        )
        self.assertEqual(
            monitor_resources.parse_stats_lines(rows, "loop-measurement-"),
            [{"memory_bytes": 10 * 1024**2, "cpu_percent": 20.0}],
        )

    def test_meminfo_requires_and_converts_core_counters(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "meminfo"
            path.write_text(
                "MemTotal: 100 kB\nMemAvailable: 60 kB\n"
                "SwapTotal: 20 kB\nSwapFree: 15 kB\n",
                encoding="utf-8",
            )
            self.assertEqual(
                monitor_resources.read_meminfo(path),
                {
                    "MemTotal": 102400,
                    "MemAvailable": 61440,
                    "SwapTotal": 20480,
                    "SwapFree": 15360,
                },
            )

    def test_monitor_records_peaks_without_container_identity(self):
        host = {
            "memory_total_bytes": 1000,
            "memory_available_bytes": 600,
            "swap_total_bytes": 100,
            "swap_used_bytes": 5,
            "load_1m": 1.25,
        }
        containers = [
            {"memory_bytes": 20, "cpu_percent": 10.0},
            {"memory_bytes": 30, "cpu_percent": 25.0},
        ]
        with (
            mock.patch.object(
                monitor_resources, "process_alive", side_effect=[True, False]
            ),
            mock.patch.object(
                monitor_resources, "docker_samples", return_value=containers
            ),
            mock.patch.object(monitor_resources, "host_sample", return_value=host),
            mock.patch.object(monitor_resources.time, "sleep"),
        ):
            record = monitor_resources.monitor(123, "loop-measurement-", 0.1)
        self.assertTrue(record["passed"])
        self.assertEqual(record["peak_concurrent_containers"], 2)
        self.assertEqual(record["peak_single_container_memory_bytes"], 30)
        self.assertEqual(record["peak_total_container_memory_bytes"], 50)
        self.assertEqual(record["peak_container_cpu_percent"], 25.0)
        self.assertNotIn("container_names", record)

    def test_a_zombie_runner_is_finished_before_the_shell_reaps_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            stat_path = Path(tmp) / "stat"
            stat_path.write_text("123 (python runner) Z 1 2 3\n", encoding="utf-8")
            with mock.patch.object(monitor_resources.Path, "exists", return_value=True), mock.patch.object(
                monitor_resources.Path, "read_text", return_value=stat_path.read_text()
            ):
                self.assertFalse(monitor_resources.process_alive(123))

    def test_atomic_write_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "record.json"
            monitor_resources.atomic_write(path, {"passed": True})
            with self.assertRaisesRegex(RuntimeError, "overwrite"):
                monitor_resources.atomic_write(path, {"passed": False})


if __name__ == "__main__":
    unittest.main()
