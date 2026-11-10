from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Entity:
    entity_id: int
    x: float
    y: float
    vx: float = 0.0
    vy: float = 0.0
    move_x: int = 0
    move_y: int = 0
    buttons: int = 0
    last_input_seq: int = 0


class World:
    def __init__(self, width: int, height: int, player_speed: float) -> None:
        self.width = float(width)
        self.height = float(height)
        self.player_speed = float(player_speed)
        self.entities: dict[int, Entity] = {}

    def spawn(self, entity_id: int, x: float, y: float) -> Entity:
        entity = Entity(entity_id=entity_id, x=x, y=y)
        self.entities[entity_id] = entity
        return entity

    def remove(self, entity_id: int) -> None:
        self.entities.pop(entity_id, None)

    def apply_input(self, entity_id: int, input_seq: int, move_x: int, move_y: int, buttons: int) -> None:
        entity = self.entities.get(entity_id)
        if entity is None or input_seq <= entity.last_input_seq:
            return
        entity.last_input_seq = input_seq
        entity.move_x = max(-1, min(1, int(move_x)))
        entity.move_y = max(-1, min(1, int(move_y)))
        entity.buttons = int(buttons) & 0xFF

    def tick(self, dt: float) -> None:
        for entity in self.entities.values():
            mx, my = entity.move_x, entity.move_y
            if mx == 0 and my == 0:
                entity.vx = 0.0
                entity.vy = 0.0
                continue
            length = math.hypot(mx, my)
            entity.vx = (mx / length) * self.player_speed
            entity.vy = (my / length) * self.player_speed
            entity.x = _clamp(entity.x + entity.vx * dt, 0.0, self.width)
            entity.y = _clamp(entity.y + entity.vy * dt, 0.0, self.height)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
