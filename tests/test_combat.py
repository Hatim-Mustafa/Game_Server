from __future__ import annotations

import unittest

from netcode_lab.protocol import messages as proto
from netcode_lab.protocol.messages import Button, EntityState, Snapshot
from netcode_lab.server.world import World

DT = 1.0 / 30.0


def make_world() -> World:
    return World(
        1024,
        768,
        200.0,
        max_health=100,
        attack_damage=10,
        attack_range=90.0,
        attack_cooldown=0.55,
        attack_duration=0.18,
    )


class CombatTest(unittest.TestCase):
    def test_attack_in_range_damages_target(self) -> None:
        world = make_world()
        attacker = world.spawn(1, 500, 500)
        target = world.spawn(2, 550, 500)
        world.apply_input(1, 1, 0, 0, int(Button.ATTACK))
        world.tick(DT)
        self.assertEqual(target.health, 90)
        self.assertGreater(attacker.attack_active, 0.0)

    def test_attack_out_of_range_does_no_damage(self) -> None:
        world = make_world()
        world.spawn(1, 500, 500)
        target = world.spawn(2, 700, 500)
        world.apply_input(1, 1, 0, 0, int(Button.ATTACK))
        world.tick(DT)
        self.assertEqual(target.health, 100)

    def test_cooldown_blocks_rapid_attacks(self) -> None:
        world = make_world()
        world.spawn(1, 500, 500)
        target = world.spawn(2, 550, 500)

        world.apply_input(1, 1, 0, 0, int(Button.ATTACK))
        world.tick(DT)
        self.assertEqual(target.health, 90)

        world.apply_input(1, 2, 0, 0, 0)
        world.tick(DT)
        world.apply_input(1, 3, 0, 0, int(Button.ATTACK))
        world.tick(DT)
        self.assertEqual(target.health, 90, "attack during cooldown should be ignored")

        world.apply_input(1, 4, 0, 0, 0)
        for _ in range(30):
            world.tick(DT)
        world.apply_input(1, 5, 0, 0, int(Button.ATTACK))
        world.tick(DT)
        self.assertEqual(target.health, 80, "attack should work once cooldown expires")

    def test_damage_floors_at_zero_and_marks_game_over(self) -> None:
        world = make_world()
        world.spawn(1, 500, 500)
        target = world.spawn(2, 550, 500)
        target.health = 5
        world.apply_input(1, 1, 0, 0, int(Button.ATTACK))
        world.tick(DT)
        self.assertEqual(target.health, 0)
        self.assertTrue(world.game_over)

    def test_dead_entity_cannot_move(self) -> None:
        world = make_world()
        entity = world.spawn(1, 500, 500)
        entity.health = 0
        world.apply_input(1, 1, 1, 0, 0)
        world.tick(DT)
        self.assertEqual(entity.x, 500)
        self.assertEqual(entity.vx, 0.0)

    def test_rematch_restores_health_and_position(self) -> None:
        world = make_world()
        entity = world.spawn(1, 500, 500)
        world.spawn(2, 750, 500)
        entity.x = 900
        entity.health = 0
        world.apply_input(1, 1, 0, 0, int(Button.REMATCH))
        self.assertTrue(world.rematch_requested)
        world.rematch()
        self.assertEqual(entity.health, 100)
        self.assertEqual((entity.x, entity.y), (entity.spawn_x, entity.spawn_y))
        self.assertFalse(world.game_over)


class SnapshotCombatFieldTest(unittest.TestCase):
    def test_health_and_flags_round_trip(self) -> None:
        snapshot = Snapshot(
            snapshot_id=5,
            base_snapshot_id=0,
            entities=(
                EntityState(1, 100, 200, 0, 0, health=37, flags=proto.FLAG_ATTACKING),
                EntityState(2, 900, 400, 0, 0, health=0, flags=proto.FLAG_DEAD),
            ),
        )
        header = proto.Header(proto.MsgType.SNAPSHOT, seq=9)
        _, payload = proto.decode(proto.pack_snapshot(header, snapshot))
        self.assertEqual(proto.parse_snapshot(payload), snapshot)

    def test_protocol_version_is_five(self) -> None:
        self.assertEqual(proto.VERSION, 5)


if __name__ == "__main__":
    unittest.main()
