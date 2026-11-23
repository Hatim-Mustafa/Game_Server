from __future__ import annotations

import bisect
import time
from dataclasses import dataclass

from netcode_lab.client.prediction import SEQ_MASK, seq_newer
from netcode_lab.protocol.messages import EntityState, Snapshot


@dataclass(frozen=True)
class _TimedSnapshot:
    tick: int
    snapshot_id: int
    entities: dict[int, EntityState]


class SnapshotInterpolator:
    """Samples buffered server snapshots on a delayed server-tick timeline."""

    def __init__(
        self,
        tick_rate: float,
        interpolation_delay: float = 0.1,
        max_extrapolation: float = 0.1,
        max_snapshots: int = 32,
        world_width: float = 1024.0,
        world_height: float = 768.0,
    ) -> None:
        if tick_rate <= 0:
            raise ValueError("tick_rate must be positive")
        if interpolation_delay < 0:
            raise ValueError("interpolation_delay cannot be negative")
        if max_extrapolation < 0:
            raise ValueError("max_extrapolation cannot be negative")
        if max_snapshots < 2:
            raise ValueError("max_snapshots must be at least 2")

        self.tick_rate = float(tick_rate)
        self.interpolation_delay = float(interpolation_delay)
        self.max_extrapolation = float(max_extrapolation)
        self.max_snapshots = max_snapshots
        self.world_width = float(world_width)
        self.world_height = float(world_height)
        self._snapshots: list[_TimedSnapshot] = []
        self._latest_raw_tick: int | None = None
        self._latest_unwrapped_tick = 0
        self._clock_tick = 0
        self._clock_received_at = 0.0

    @property
    def buffered_snapshot_count(self) -> int:
        return len(self._snapshots)

    def add_snapshot(
        self,
        snapshot: Snapshot,
        server_tick: int,
        received_at: float | None = None,
    ) -> None:
        received_at = time.monotonic() if received_at is None else received_at
        raw_tick = server_tick & SEQ_MASK
        unwrapped_tick, is_newest = self._unwrap_tick(raw_tick)

        if is_newest:
            self._clock_tick = unwrapped_tick
            self._clock_received_at = received_at

        timed = _TimedSnapshot(
            tick=unwrapped_tick,
            snapshot_id=snapshot.snapshot_id,
            entities={entity.entity_id: entity for entity in snapshot.entities},
        )
        ticks = [item.tick for item in self._snapshots]
        index = bisect.bisect_left(ticks, unwrapped_tick)
        if (
            index < len(self._snapshots)
            and self._snapshots[index].tick == unwrapped_tick
        ):
            previous = self._snapshots[index]
            if seq_newer(snapshot.snapshot_id, previous.snapshot_id):
                self._snapshots[index] = timed
            return

        self._snapshots.insert(index, timed)
        if len(self._snapshots) > self.max_snapshots:
            del self._snapshots[: len(self._snapshots) - self.max_snapshots]

    def sample(self, now: float | None = None) -> tuple[EntityState, ...]:
        if not self._snapshots:
            return ()

        now = time.monotonic() if now is None else now
        estimated_server_tick = (
            self._clock_tick + max(0.0, now - self._clock_received_at) * self.tick_rate
        )
        target_tick = estimated_server_tick - self.interpolation_delay * self.tick_rate

        ticks = [item.tick for item in self._snapshots]
        if target_tick <= ticks[0]:
            return self._ordered(self._snapshots[0].entities)
        if target_tick >= ticks[-1]:
            elapsed = min(
                (target_tick - ticks[-1]) / self.tick_rate,
                self.max_extrapolation,
            )
            return self._extrapolate(self._snapshots[-1].entities, elapsed)

        after_index = bisect.bisect_right(ticks, target_tick)
        before = self._snapshots[after_index - 1]
        after = self._snapshots[after_index]
        alpha = (target_tick - before.tick) / (after.tick - before.tick)
        entities: dict[int, EntityState] = {}

        for entity_id, earlier in before.entities.items():
            later = after.entities.get(entity_id)
            if later is None:
                entities[entity_id] = earlier
                continue
            entities[entity_id] = EntityState(
                entity_id=entity_id,
                x=round(_lerp(earlier.x, later.x, alpha)),
                y=round(_lerp(earlier.y, later.y, alpha)),
                vx=round(_lerp(earlier.vx, later.vx, alpha)),
                vy=round(_lerp(earlier.vy, later.vy, alpha)),
                health=earlier.health,
                flags=earlier.flags,
            )

        return self._ordered(entities)

    def _unwrap_tick(self, raw_tick: int) -> tuple[int, bool]:
        if self._latest_raw_tick is None:
            self._latest_raw_tick = raw_tick
            self._latest_unwrapped_tick = raw_tick
            return self._latest_unwrapped_tick, True

        if raw_tick == self._latest_raw_tick:
            return self._latest_unwrapped_tick, False

        if seq_newer(raw_tick, self._latest_raw_tick):
            delta = (raw_tick - self._latest_raw_tick) & SEQ_MASK
            self._latest_raw_tick = raw_tick
            self._latest_unwrapped_tick += delta
            return self._latest_unwrapped_tick, True

        ticks_behind = (self._latest_raw_tick - raw_tick) & SEQ_MASK
        return self._latest_unwrapped_tick - ticks_behind, False

    def _extrapolate(
        self,
        entities: dict[int, EntityState],
        elapsed: float,
    ) -> tuple[EntityState, ...]:
        if elapsed <= 0:
            return self._ordered(entities)
        extrapolated = {
            entity_id: EntityState(
                entity_id=entity.entity_id,
                x=round(_clamp(entity.x + entity.vx * elapsed, 0.0, self.world_width)),
                y=round(_clamp(entity.y + entity.vy * elapsed, 0.0, self.world_height)),
                vx=entity.vx,
                vy=entity.vy,
                health=entity.health,
                flags=entity.flags,
            )
            for entity_id, entity in entities.items()
        }
        return self._ordered(extrapolated)

    @staticmethod
    def _ordered(entities: dict[int, EntityState]) -> tuple[EntityState, ...]:
        return tuple(entities[entity_id] for entity_id in sorted(entities))


def _lerp(start: float, end: float, amount: float) -> float:
    return start + (end - start) * amount


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
