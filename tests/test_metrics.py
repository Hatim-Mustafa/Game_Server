from __future__ import annotations

import unittest

from netcode_lab.client.metrics import ClientMetrics


class ClientMetricsTest(unittest.TestCase):
    def test_snapshot_intervals_stalls_and_gaps(self) -> None:
        metrics = ClientMetrics(stall_threshold=0.1)
        metrics.record_snapshot(1, at=1.00)
        metrics.record_snapshot(2, at=1.05)
        metrics.record_snapshot(5, at=1.20)

        self.assertEqual(metrics.snapshots, 3)
        self.assertEqual(metrics.stalls, 1)
        self.assertEqual(metrics.snapshot_gaps, 2)
        self.assertAlmostEqual(metrics.last_snapshot_interval_ms, 150.0, places=3)
        self.assertGreater(metrics.snapshot_jitter_ms, 0.0)

    def test_bytes_and_rates(self) -> None:
        metrics = ClientMetrics()
        metrics.record_sent(100)
        metrics.record_sent(50)
        metrics.record_received(200)

        summary = metrics.summary(duration=2.0)
        self.assertEqual(summary["sent_bytes"], 150)
        self.assertEqual(summary["recv_bytes"], 200)
        self.assertAlmostEqual(summary["sent_bytes_per_s"], 75.0)
        self.assertAlmostEqual(summary["recv_bytes_per_s"], 100.0)

    def test_latency_and_position_error_means(self) -> None:
        metrics = ClientMetrics()
        metrics.record_input_latency(0.05)
        metrics.record_input_latency(0.07)
        metrics.record_position_error(10.0)
        metrics.record_position_error(30.0)

        self.assertAlmostEqual(metrics.input_latency_mean_ms, 60.0, places=3)
        self.assertAlmostEqual(metrics.position_error_mean_px, 20.0, places=3)
        self.assertEqual(len(metrics.input_latencies), 2)


if __name__ == "__main__":
    unittest.main()
