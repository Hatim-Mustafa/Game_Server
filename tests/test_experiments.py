from __future__ import annotations

import unittest

from experiments.aggregate import group_summaries
from experiments.run import Condition, parse_conditions, parse_modes


class ParseTest(unittest.TestCase):
    def test_conditions(self) -> None:
        conditions = parse_conditions("rtt20:20:0, loss5:50:5")
        self.assertEqual(
            conditions,
            [Condition("rtt20", 20.0, 0.0), Condition("loss5", 50.0, 5.0)],
        )

    def test_condition_defaults(self) -> None:
        self.assertEqual(parse_conditions("a:100"), [Condition("a", 100.0, 0.0)])
        self.assertEqual(parse_conditions("b"), [Condition("b", 0.0, 0.0)])

    def test_modes(self) -> None:
        self.assertEqual(parse_modes("c0,c2"), ["c0", "c2"])
        with self.assertRaises(ValueError):
            parse_modes("c9")

    def test_empty_conditions_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_conditions(" , ")


class AggregateTest(unittest.TestCase):
    def test_group_mean_and_std(self) -> None:
        items = [
            {
                "_condition": "rtt20",
                "_mode": "c1",
                "input_latency_mean_ms": 10.0,
                "correction_rate": 0.1,
            },
            {
                "_condition": "rtt20",
                "_mode": "c1",
                "input_latency_mean_ms": 20.0,
                "correction_rate": 0.3,
            },
            {
                "_condition": "rtt20",
                "_mode": "c0",
                "input_latency_mean_ms": 50.0,
                "correction_rate": 0.0,
            },
        ]

        grouped = group_summaries(items)
        mean, std, count = grouped[("rtt20", "c1")]["input_latency_mean_ms"]
        self.assertAlmostEqual(mean, 15.0)
        self.assertGreater(std, 0.0)
        self.assertEqual(count, 2)

        c0_mean, _, c0_count = grouped[("rtt20", "c0")]["input_latency_mean_ms"]
        self.assertAlmostEqual(c0_mean, 50.0)
        self.assertEqual(c0_count, 1)
        self.assertEqual(grouped[("rtt20", "c0")]["input_latency_mean_ms"][1], 0.0)


if __name__ == "__main__":
    unittest.main()
