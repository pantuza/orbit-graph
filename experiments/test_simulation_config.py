#!/usr/bin/env python3
"""Unit tests for simulation.json loading."""

import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from simulation_config import load_simulation_plan  # noqa: E402


class SimulationPlanTest(unittest.TestCase):
    def test_load_default_shape(self):
        plan = load_simulation_plan(
            os.path.join(_ROOT, "simulation.json"), _ROOT)
        self.assertEqual(plan.reps, 1)
        self.assertEqual(plan.profile, "full")
        self.assertEqual(plan.modes, ["ospf", "sdn"])
        self.assertEqual(len(plan.constellations), 4)
        self.assertEqual(plan.constellations[0].size_label, "6x6")
        self.assertEqual(plan.constellations[0].duration, 120)
        self.assertEqual(plan.duration_by_size()[(8, 8)], 300)

    def test_relative_out_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "plan.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({
                    "reps": 2,
                    "out_dir": "./my_results",
                    "constellations": [{"size": "6x6", "duration": 100}],
                }, fh)
            plan = load_simulation_plan(path, _ROOT)
            self.assertEqual(
                plan.out_dir,
                os.path.normpath(os.path.join(_ROOT, "my_results")),
            )

    def test_duplicate_size_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({
                    "constellations": [
                        {"size": "6x6", "duration": 100},
                        {"size": "6x6", "duration": 200},
                    ],
                }, fh)
            with self.assertRaises(ValueError):
                load_simulation_plan(path, _ROOT)


if __name__ == "__main__":
    unittest.main()
