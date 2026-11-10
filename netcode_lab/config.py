from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 9000
    tick_rate: float = 30.0
    snapshot_rate: float = 20.0
    world_width: int = 1024
    world_height: int = 768
    player_speed: float = 200.0

    @property
    def tick_dt(self) -> float:
        return 1.0 / self.tick_rate

    @property
    def snapshot_interval(self) -> float:
        return 1.0 / self.snapshot_rate
