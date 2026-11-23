from __future__ import annotations

import asyncio
import random
import unittest

from netcode_lab.client.client import ClientConfig, GameClient
from netcode_lab.client.prediction import Predictor, seq_newer
from netcode_lab.client.predicted_client import PredictedClient
from netcode_lab.config import ServerConfig
from netcode_lab.server.server import GameServer
from netcode_lab.server.world import World

CFG = ServerConfig()


def make_predictor() -> Predictor:
    return Predictor(
        world_width=CFG.world_width,
        world_height=CFG.world_height,
        player_speed=CFG.player_speed,
        tick_dt=CFG.tick_dt,
    )


class SeqTest(unittest.TestCase):
    def test_seq_newer_with_wraparound(self) -> None:
        self.assertTrue(seq_newer(5, 4))
        self.assertFalse(seq_newer(4, 5))
        self.assertFalse(seq_newer(4, 4))
        self.assertTrue(seq_newer(1, 0xFFFFFFFF))


class PredictorTest(unittest.TestCase):
    def test_step_matches_server_world(self) -> None:
        """Client movement must be identical to the server's or every snapshot corrects."""
        rng = random.Random(7)
        world = World(CFG.world_width, CFG.world_height, CFG.player_speed)
        world.spawn(1, 500.0, 400.0)
        pred = make_predictor()
        pred.x, pred.y = 500.0, 400.0
        for seq in range(1, 400):
            mx, my = rng.choice((-1, 0, 1)), rng.choice((-1, 0, 1))
            world.apply_input(1, seq, mx, my, 0)
            world.tick(CFG.tick_dt)
            pred.step(mx, my)
            entity = world.entities[1]
            self.assertAlmostEqual(entity.x, pred.x, places=6)
            self.assertAlmostEqual(entity.y, pred.y, places=6)

    def test_input_applies_immediately_after_first_snapshot(self) -> None:
        pred = make_predictor()
        pred.reconcile(100, 100, 0)
        pred.apply_local_input(1, 1, 0)
        self.assertGreater(pred.x, 100)

    def test_inputs_before_first_snapshot_are_replayed(self) -> None:
        pred = make_predictor()
        pred.apply_local_input(1, 1, 0)
        pred.apply_local_input(2, 1, 0)
        self.assertFalse(pred.initialized)
        pred.reconcile(100, 100, 0)
        self.assertAlmostEqual(pred.x, 100 + 2 * CFG.player_speed * CFG.tick_dt)

    def test_consistent_server_causes_no_correction(self) -> None:
        pred = make_predictor()
        pred.reconcile(100, 100, 0)
        for seq in range(1, 6):
            pred.apply_local_input(seq, 1, 0)
        step = CFG.player_speed * CFG.tick_dt
        # server has applied inputs 1..3 (3 ticks); 4 and 5 still pending
        error = pred.reconcile(100 + 3 * step, 100, 3)
        self.assertLess(error, 1e-6)
        self.assertEqual([p.input_seq for p in pred.pending], [4, 5])
        self.assertEqual(pred.stats.corrections, 0)

    def test_server_one_tick_behind_is_not_a_correction(self) -> None:
        """Input 3 received but the server has not ticked with it yet."""
        pred = make_predictor()
        pred.reconcile(100, 100, 0)
        for seq in range(1, 6):
            pred.apply_local_input(seq, 1, 0)
        step = CFG.player_speed * CFG.tick_dt
        error = pred.reconcile(100 + 2 * step, 100, 3)
        self.assertLess(error, 1e-6)
        self.assertEqual(pred.stats.corrections, 0)

    def test_real_disagreement_is_corrected_and_counted(self) -> None:
        pred = make_predictor()
        pred.reconcile(100, 100, 0)
        for seq in range(1, 4):
            pred.apply_local_input(seq, 1, 0)
        # server says the player is somewhere else entirely (e.g. pushed by a rule)
        error = pred.reconcile(300, 300, 3)
        self.assertGreater(error, 100)
        self.assertEqual((pred.x, pred.y), (300.0, 300.0))
        self.assertEqual(pred.stats.corrections, 1)
        self.assertEqual(pred.stats.max_px, error)

    def test_lost_input_at_direction_change_is_corrected(self) -> None:
        """Server never got input 3 (turn downwards), so it kept moving right."""
        pred = make_predictor()
        pred.reconcile(100, 100, 0)
        pred.apply_local_input(1, 1, 0)
        pred.apply_local_input(2, 1, 0)
        pred.apply_local_input(3, 0, 1)
        pred.apply_local_input(4, 0, 1)
        step = CFG.player_speed * CFG.tick_dt
        # server: ticks with 1, 2, then input 3 lost -> one more tick right, then 4
        world = World(CFG.world_width, CFG.world_height, CFG.player_speed)
        world.spawn(1, 100, 100)
        for seq, (mx, my) in ((1, (1, 0)), (2, (1, 0)), (2, (1, 0)), (4, (0, 1))):
            world.apply_input(1, seq, mx, my, 0)
            world.tick(CFG.tick_dt)
        e = world.entities[1]
        error = pred.reconcile(e.x, e.y, 4)
        self.assertAlmostEqual(error, step * 2 ** 0.5, places=3)
        self.assertEqual(pred.stats.corrections, 1)

    def test_old_ack_does_not_rewind(self) -> None:
        pred = make_predictor()
        pred.reconcile(100, 100, 0)
        for seq in range(1, 6):
            pred.apply_local_input(seq, 1, 0)
        step = CFG.player_speed * CFG.tick_dt
        pred.reconcile(100 + 4 * step, 100, 4)
        pred.reconcile(100 + 4 * step, 100, 2)  # stale ack arrives late
        self.assertEqual(pred.last_acked_input, 4)
        self.assertEqual([p.input_seq for p in pred.pending], [5])


async def _start_server() -> tuple[GameServer, asyncio.Task, int]:
    server = GameServer(ServerConfig(host="127.0.0.1", port=0))
    task = asyncio.create_task(server.run())
    for _ in range(200):
        if server.transport is not None:
            break
        await asyncio.sleep(0.01)
    return server, task, server.transport.get_extra_info("sockname")[1]


class PredictedClientIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_c1_moves_before_server_confirms_and_stays_in_sync(self) -> None:
        server, server_task, port = await _start_server()
        client = PredictedClient(ClientConfig(host="127.0.0.1", port=port))
        client_task = asyncio.create_task(client.run(duration=3.0))
        try:
            for _ in range(300):
                if client.get_entity() is not None:
                    break
                await asyncio.sleep(0.01)
            start = client.get_entity()
            self.assertIsNotNone(start)
            server_start = client.get_server_entity()

            client.set_input(1, 0)
            await asyncio.sleep(0.04)  # a bit over one input interval
            self.assertGreater(client.get_entity().x, start.x, "prediction did not move")

            # keep moving a while, then check the prediction agrees with the server
            await asyncio.sleep(1.0)
            client.set_input(0, 0)
            await asyncio.sleep(0.4)
            predicted = client.get_entity()
            truth = server.world.entities[client.player_id]
            self.assertLessEqual(abs(predicted.x - truth.x), 1.0)
            self.assertLessEqual(abs(predicted.y - truth.y), 1.0)
            self.assertGreater(truth.x, server_start.x)
            self.assertGreater(client.predictor.stats.reconciliations, 20)
            self.assertLessEqual(client.predictor.stats.rate, 0.1)
        finally:
            client_task.cancel()
            server_task.cancel()
            await asyncio.gather(client_task, server_task, return_exceptions=True)

    async def test_c0_client_still_works(self) -> None:
        _, server_task, port = await _start_server()
        client = GameClient(ClientConfig(host="127.0.0.1", port=port))
        client_task = asyncio.create_task(client.run(duration=1.0))
        try:
            await asyncio.wait_for(client.joined.wait(), timeout=2.0)
            self.assertFalse(hasattr(client, "predictor"))
        finally:
            client_task.cancel()
            server_task.cancel()
            await asyncio.gather(client_task, server_task, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
