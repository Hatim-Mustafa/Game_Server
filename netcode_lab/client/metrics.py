from __future__ import annotations

import statistics
import time
from collections import deque


class ClientMetrics:
    def __init__(self, stall_threshold: float = 0.25, max_samples: int = 20000) -> None:
        self.stall_threshold = float(stall_threshold)
        self.sent_bytes = 0
        self.recv_bytes = 0
        self.snapshots = 0
        self.stalls = 0
        self.snapshot_gaps = 0
        self._last_snapshot_id = 0
        self._last_snapshot_at: float | None = None
        self.snapshot_intervals: deque[float] = deque(maxlen=max_samples)
        self.input_latencies: deque[float] = deque(maxlen=max_samples)
        self.position_errors: deque[float] = deque(maxlen=max_samples)
        self.started_at = time.monotonic()
        self.finished_at: float | None = None

    def record_sent(self, size: int) -> None:
        self.sent_bytes += int(size)

    def record_received(self, size: int) -> None:
        self.recv_bytes += int(size)

    def record_snapshot(self, snapshot_id: int, at: float | None = None) -> None:
        at = time.monotonic() if at is None else at
        self.snapshots += 1
        if self._last_snapshot_at is not None:
            interval = at - self._last_snapshot_at
            self.snapshot_intervals.append(interval)
            if interval > self.stall_threshold:
                self.stalls += 1
        if self._last_snapshot_id and snapshot_id > self._last_snapshot_id:
            self.snapshot_gaps += snapshot_id - self._last_snapshot_id - 1
        if snapshot_id > self._last_snapshot_id:
            self._last_snapshot_id = snapshot_id
        self._last_snapshot_at = at

    def record_input_latency(self, seconds: float) -> None:
        self.input_latencies.append(max(0.0, float(seconds)))

    def record_position_error(self, pixels: float) -> None:
        self.position_errors.append(max(0.0, float(pixels)))

    def finish(self) -> None:
        self.finished_at = time.monotonic()

    @property
    def elapsed(self) -> float:
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return max(0.0, end - self.started_at)

    @property
    def last_snapshot_interval_ms(self) -> float:
        return self.snapshot_intervals[-1] * 1000.0 if self.snapshot_intervals else 0.0

    @property
    def snapshot_jitter_ms(self) -> float:
        if len(self.snapshot_intervals) < 2:
            return 0.0
        return statistics.pstdev(self.snapshot_intervals) * 1000.0

    @property
    def input_latency_mean_ms(self) -> float:
        if not self.input_latencies:
            return 0.0
        return statistics.fmean(self.input_latencies) * 1000.0

    @property
    def input_latency_last_ms(self) -> float:
        return self.input_latencies[-1] * 1000.0 if self.input_latencies else 0.0

    @property
    def position_error_mean_px(self) -> float:
        if not self.position_errors:
            return 0.0
        return statistics.fmean(self.position_errors)

    @property
    def position_error_last_px(self) -> float:
        return self.position_errors[-1] if self.position_errors else 0.0

    def summary(self, duration: float | None = None) -> dict[str, float | int]:
        duration = self.elapsed if duration is None else max(duration, 1e-9)
        return {
            "snapshots": self.snapshots,
            "snapshot_rate_hz": self.snapshots / duration,
            "snapshot_jitter_ms": self.snapshot_jitter_ms,
            "stalls": self.stalls,
            "snapshot_gaps": self.snapshot_gaps,
            "sent_bytes": self.sent_bytes,
            "recv_bytes": self.recv_bytes,
            "sent_bytes_per_s": self.sent_bytes / duration,
            "recv_bytes_per_s": self.recv_bytes / duration,
            "input_latency_samples": len(self.input_latencies),
            "input_latency_mean_ms": self.input_latency_mean_ms,
            "input_latency_last_ms": self.input_latency_last_ms,
            "position_error_mean_px": self.position_error_mean_px,
            "position_error_last_px": self.position_error_last_px,
        }
