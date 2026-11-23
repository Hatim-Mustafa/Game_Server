from __future__ import annotations

import asyncio
import unittest

from netcode_lab.client.client import ClientConfig, GameClient
from netcode_lab.config import ServerConfig
from netcode_lab.server.server import GameServer


class ClientServerIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_join_and_movement(self) -> None:
        server = GameServer(
            ServerConfig(host="127.0.0.1", port=0, tick_rate=60, snapshot_rate=30)
        )
        server_task = asyncio.create_task(server.run())
        client_task: asyncio.Task | None = None
        try:
            for _ in range(200):
                if server.transport is not None:
                    break
                await asyncio.sleep(0.01)
            self.assertIsNotNone(server.transport)
            port = server.transport.get_extra_info("sockname")[1]

            client = GameClient(ClientConfig(host="127.0.0.1", port=port, input_rate=60))
            client_task = asyncio.create_task(client.run(duration=2.0))
            await asyncio.wait_for(client.joined.wait(), timeout=2.0)
            self.assertEqual(client.player_id, 1)

            for _ in range(200):
                if client.latest_snapshot is not None:
                    break
                await asyncio.sleep(0.01)
            start = client.get_entity()
            self.assertIsNotNone(start)

            client.set_input(1, 0)
            moved = None
            for _ in range(200):
                await asyncio.sleep(0.01)
                entity = client.get_entity()
                if entity is not None and entity.x > start.x:
                    moved = entity
                    break
            self.assertIsNotNone(moved, "player did not move after sending input")
        finally:
            if client_task is not None:
                client_task.cancel()
                await asyncio.gather(client_task, return_exceptions=True)
            server_task.cancel()
            await asyncio.gather(server_task, return_exceptions=True)


class PlayerSlotTest(unittest.IsolatedAsyncioTestCase):
    async def test_claim_and_denial(self) -> None:
        server = GameServer(
            ServerConfig(host="127.0.0.1", port=0, tick_rate=60, snapshot_rate=30)
        )
        server_task = asyncio.create_task(server.run())
        tasks: list[asyncio.Task] = []
        try:
            for _ in range(200):
                if server.transport is not None:
                    break
                await asyncio.sleep(0.01)
            port = server.transport.get_extra_info("sockname")[1]

            first = GameClient(
                ClientConfig(host="127.0.0.1", port=port, player_slot=2)
            )
            second = GameClient(
                ClientConfig(host="127.0.0.1", port=port, player_slot=2)
            )
            tasks.append(asyncio.create_task(first.run(duration=1.5)))
            await asyncio.wait_for(first.joined.wait(), timeout=2.0)
            self.assertEqual(first.player_id, 2)

            tasks.append(asyncio.create_task(second.run(duration=1.5)))
            for _ in range(200):
                if second.rejected:
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(second.rejected, "second claimant should be denied")
            self.assertIsNone(second.player_id)
        finally:
            for task in tasks:
                task.cancel()
            server_task.cancel()
            await asyncio.gather(*tasks, server_task, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
