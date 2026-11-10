from __future__ import annotations

import unittest

from netcode_lab.server.world import World


class WorldTest(unittest.TestCase):
    def test_spawn_and_move(self) -> None:
        world = World(100, 100, 10.0)
        world.spawn(1, 50, 50)
        world.apply_input(1, 1, 1, 0, 0)
        world.tick(1.0)
        entity = world.entities[1]
        self.assertAlmostEqual(entity.x, 60.0, places=3)
        self.assertAlmostEqual(entity.y, 50.0, places=3)
        self.assertAlmostEqual(entity.vx, 10.0, places=3)

    def test_diagonal_is_normalised(self) -> None:
        world = World(1000, 1000, 10.0)
        world.spawn(1, 500, 500)
        world.apply_input(1, 1, 1, 1, 0)
        world.tick(1.0)
        entity = world.entities[1]
        self.assertAlmostEqual((entity.vx ** 2 + entity.vy ** 2) ** 0.5, 10.0, places=3)

    def test_stale_input_is_ignored(self) -> None:
        world = World(100, 100, 10.0)
        world.spawn(1, 50, 50)
        world.apply_input(1, 5, 1, 0, 0)
        world.apply_input(1, 4, -1, 0, 0)
        self.assertEqual(world.entities[1].move_x, 1)

    def test_bounds_are_clamped(self) -> None:
        world = World(100, 100, 1000.0)
        world.spawn(1, 99, 50)
        world.apply_input(1, 1, 1, 0, 0)
        world.tick(1.0)
        self.assertEqual(world.entities[1].x, 100.0)

    def test_remove(self) -> None:
        world = World(100, 100, 10.0)
        world.spawn(1, 50, 50)
        world.remove(1)
        self.assertNotIn(1, world.entities)


if __name__ == "__main__":
    unittest.main()
