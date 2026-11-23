from __future__ import annotations

import math
from dataclasses import dataclass

from netcode_lab.protocol.messages import Button


@dataclass
class Entity:
    entity_id: int
    x: float
    y: float
    spawn_x: float = 0.0
    spawn_y: float = 0.0
    vx: float = 0.0
    vy: float = 0.0
    move_x: int = 0
    move_y: int = 0
    buttons: int = 0
    last_input_seq: int = 0
    health: int = 100
    attack_held: bool = False
    attack_pending: bool = False
    attack_cooldown: float = 0.0
    attack_active: float = 0.0
    rematch_held: bool = False


class World:
    def __init__(
        self,
        width: int,
        height: int,
        player_speed: float,
        max_health: int = 100,
        attack_damage: int = 10,
        attack_range: float = 90.0,
        attack_cooldown: float = 0.55,
        attack_duration: float = 0.18,
    ) -> None:
        self.width = float(width)
        self.height = float(height)
        self.player_speed = float(player_speed)
        self.max_health = int(max_health)
        self.attack_damage = int(attack_damage)
        self.attack_range = float(attack_range)
        self.attack_cooldown = float(attack_cooldown)
        self.attack_duration = float(attack_duration)
        self.entities: dict[int, Entity] = {}
        self.rematch_requested = False

    def spawn(self, entity_id: int, x: float, y: float) -> Entity:
        entity = Entity(
            entity_id=entity_id,
            x=x,
            y=y,
            spawn_x=x,
            spawn_y=y,
            health=self.max_health,
        )
        self.entities[entity_id] = entity
        return entity

    def remove(self, entity_id: int) -> None:
        self.entities.pop(entity_id, None)

    @property
    def game_over(self) -> bool:
        return bool(self.entities) and any(e.health <= 0 for e in self.entities.values())

    def rematch(self) -> None:
        for entity in self.entities.values():
            entity.x = entity.spawn_x
            entity.y = entity.spawn_y
            entity.vx = 0.0
            entity.vy = 0.0
            entity.move_x = 0
            entity.move_y = 0
            entity.buttons = 0
            entity.health = self.max_health
            entity.attack_held = False
            entity.attack_pending = False
            entity.attack_cooldown = 0.0
            entity.attack_active = 0.0
            entity.rematch_held = False
        self.rematch_requested = False

    def apply_input(
        self,
        entity_id: int,
        input_seq: int,
        move_x: int,
        move_y: int,
        buttons: int,
    ) -> None:
        entity = self.entities.get(entity_id)
        if entity is None or input_seq <= entity.last_input_seq:
            return
        entity.last_input_seq = input_seq
        entity.move_x = max(-1, min(1, int(move_x)))
        entity.move_y = max(-1, min(1, int(move_y)))
        entity.buttons = int(buttons) & 0xFF

        attack_pressed = bool(entity.buttons & Button.ATTACK)
        if (
            attack_pressed
            and not entity.attack_held
            and entity.attack_cooldown <= 0.0
            and entity.health > 0
        ):
            entity.attack_pending = True
        entity.attack_held = attack_pressed

        rematch_pressed = bool(entity.buttons & Button.REMATCH)
        if rematch_pressed and not entity.rematch_held:
            self.rematch_requested = True
        entity.rematch_held = rematch_pressed

    def tick(self, dt: float) -> None:
        for entity in self.entities.values():
            if entity.attack_cooldown > 0.0:
                entity.attack_cooldown = max(0.0, entity.attack_cooldown - dt)
            if entity.attack_active > 0.0:
                entity.attack_active = max(0.0, entity.attack_active - dt)

        self._resolve_attacks()

        for entity in self.entities.values():
            if entity.health <= 0:
                entity.vx = 0.0
                entity.vy = 0.0
                continue
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

    def _resolve_attacks(self) -> None:
        for attacker in self.entities.values():
            if not attacker.attack_pending:
                continue
            attacker.attack_pending = False
            if attacker.health <= 0:
                continue
            attacker.attack_cooldown = self.attack_cooldown
            attacker.attack_active = self.attack_duration
            target = self._nearest_target(attacker)
            if target is not None:
                target.health = max(0, target.health - self.attack_damage)

    def _nearest_target(self, attacker: Entity) -> Entity | None:
        best: Entity | None = None
        best_distance = self.attack_range
        for other in self.entities.values():
            if other is attacker or other.health <= 0:
                continue
            distance = math.hypot(other.x - attacker.x, other.y - attacker.y)
            if distance <= best_distance:
                best = other
                best_distance = distance
        return best


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
