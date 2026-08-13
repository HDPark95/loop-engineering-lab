#!/usr/bin/env python3
"""Tests for the registered block-level power calculation."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from analysis import power_sim


ROOT = Path(__file__).resolve().parents[1]


class PowerSimulationTest(unittest.TestCase):
    def test_simulation_uses_the_registered_exact_sign_flip_decision(self):
        with mock.patch.object(
            power_sim, "exact_sign_flip_p", side_effect=(0.02, 0.03)
        ) as statistic:
            value = power_sim.simulate(
                effect=0.2,
                block_sd=0.15,
                blocks=10,
                alpha=0.025,
                trials=2,
                seed=7,
            )
        self.assertEqual(value, 0.5)
        self.assertEqual(statistic.call_count, 2)
        self.assertTrue(all(len(call.args[0]) == 10 for call in statistic.call_args_list))

    def test_invalid_blocks_power_and_cli_seed_fail_cleanly(self):
        with self.assertRaisesRegex(ValueError, "blocks must be positive"):
            power_sim.standard_error(0.15, 0)
        with self.assertRaisesRegex(ValueError, "supported power values"):
            power_sim.mde(0.15, 10, 0.025, power=0.81)
        result = subprocess.run(
            [sys.executable, str(ROOT / "analysis" / "power_sim.py"), "--seeds", "0"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--seeds must be at least 1", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
