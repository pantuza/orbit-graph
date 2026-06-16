#!/usr/bin/env python3
"""Unit tests for compare_batch helpers."""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from compare_batch import (  # noqa: E402
    _format_elapsed,
    _merge_appended_raw,
    _parse_durations,
    _parse_sizes,
)


class ParseSizesTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(_parse_sizes(""), [(None, None)])

    def test_list(self):
        self.assertEqual(_parse_sizes("6x6,8x8"), [(6, 6), (8, 8)])


class ParseDurationsTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(_parse_durations(""), {})

    def test_map(self):
        self.assertEqual(
            _parse_durations("6x6=120,8x8=300"),
            {(6, 6): 120, (8, 8): 300},
        )

    def test_invalid_token(self):
        with self.assertRaises(SystemExit):
            _parse_durations("6x6-120")

    def test_duplicate(self):
        with self.assertRaises(SystemExit):
            _parse_durations("6x6=120,6x6=200")


class FormatElapsedTest(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(_format_elapsed(12.34), "12.34s")

    def test_minutes(self):
        self.assertEqual(_format_elapsed(644.31), "10m 44.31s")

    def test_hours(self):
        self.assertEqual(_format_elapsed(3847.52), "1h 4m 7.52s")


class MergeAppendTest(unittest.TestCase):
    def test_replaces_same_node_count(self):
        import tempfile

        ping_a = [{
            "mode": "sdn", "profile": "full", "nodes": 38, "rep": 1,
            "seed": 1001, "time_tag": "23", "phase": "handover",
            "loss_pct": 0.0, "avg_rtt_ms": 30.0,
        }]
        ping_b = [{
            "mode": "sdn", "profile": "full", "nodes": 66, "rep": 1,
            "seed": 1001, "time_tag": "45", "phase": "handover",
            "loss_pct": 0.0, "avg_rtt_ms": 25.0,
        }]
        ping_a2 = [{
            "mode": "sdn", "profile": "full", "nodes": 38, "rep": 1,
            "seed": 1001, "time_tag": "23", "phase": "handover",
            "loss_pct": 50.0, "avg_rtt_ms": None,
        }]
        with tempfile.TemporaryDirectory() as tmp:
            from compare_batch import _write_csv, PING_RAW_FIELDS

            _write_csv(os.path.join(tmp, "ping_raw.csv"), ping_a, PING_RAW_FIELDS)
            merged, _, _ = _merge_appended_raw(tmp, ping_b, [], [])
            _write_csv(os.path.join(tmp, "ping_raw.csv"), merged, PING_RAW_FIELDS)
            merged2, _, _ = _merge_appended_raw(tmp, ping_a2, [], [])
            self.assertEqual(len(merged), 2)
            self.assertEqual(len(merged2), 2)
            nodes38 = [r for r in merged2 if r["nodes"] == 38]
            self.assertEqual(len(nodes38), 1)
            self.assertEqual(nodes38[0]["loss_pct"], 50.0)


if __name__ == "__main__":
    unittest.main()
