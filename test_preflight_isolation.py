#!/usr/bin/env python3
"""Tests for fail-closed sandbox preflight evaluation."""

from __future__ import annotations

import unittest

import preflight_isolation


class IsolationPreflightTest(unittest.TestCase):
    def valid_probe(self) -> dict:
        return {
            "readable_oracle_paths": [],
            "network_connected": False,
            "rootfs_write_succeeded": False,
            "root_mount_read_only": True,
            "network_interfaces": ["lo"],
            "effective_uid": 65534,
            "effective_capabilities_hex": "0000000000000000",
        }

    def test_complete_isolation_probe_passes(self):
        self.assertEqual(preflight_isolation.validate_probe(self.valid_probe()), [])

    def test_each_boundary_failure_is_rejected(self):
        mutations = (
            {"readable_oracle_paths": ["/oracle/se_tasks"]},
            {"network_connected": True},
            {"rootfs_write_succeeded": True},
            {"root_mount_read_only": False},
            {"network_interfaces": ["eth0", "lo"]},
            {"effective_uid": 0},
            {"effective_capabilities_hex": "0000000000000001"},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.assertTrue(
                    preflight_isolation.validate_probe(self.valid_probe() | mutation)
                )


if __name__ == "__main__":
    unittest.main()
