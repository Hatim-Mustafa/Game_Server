from __future__ import annotations

import asyncio
import unittest

from netcode_lab.client.client import ClientConfig, GameClient
from netcode_lab.client.compressed_client import CompressedClient
from netcode_lab.config import ServerConfig
from netcode_lab.protocol import messages as proto
from netcode_lab.protocol.messages import EntityState, Header, MsgType, Snapshot
from netcode_lab.server.server import GameServer


def pack_snapshot(snapshot: Snapshot, seq: int, tick: int) -> bytes:
    return proto.pack_snapshot(Header(MsgType.SNAPSHOT, seq=seq, tick=tick), snapshot)


class RedundancyTest(unittest.TestCase):
    def test_sends_last_n_inputs_and_advertises_delta(self) -> None:
        client = CompressedClient(
            ClientConfig(input_rate=30, redundancy=3),
            ServerConfig(tick_rate=30),
        )
        records = [proto.InputRecord(i, i, 1, 0) for i in range(1, 6)]
        sent = [client._inputs_to_send(record) for record in records]

        self.assertEqual(sent[0], (records[0],))
        self.assertEqual(sent[2], tuple(records[0:3]))
        self.assertEqual(sent[4], tuple(records[2:5]))
        self.assertEqual(client.mode, "c3")
        self.assertTrue(
            client.capabilities & int(proto.Capability.DELTA_SNAPSHOTS)
        )


class DeltaReconstructionTest(unittest.TestCase):
    def test_full_then_delta_reconstructs_state(self) -> None:
        client = GameClient(ClientConfig())
        full = Snapshot(
            1,
            0,
            (
                EntityState(1, 100, 100, 0, 0),
                EntityState(2, 200, 100, 0, 0),
                EntityState(3, 300, 100, 0, 0),
            ),
        )
        delta = Snapshot(
            2,
            1,
            (
                EntityState(2, 250, 100, 0, 0),
                EntityState(4, 400, 100, 0, 0),
            ),
            removed_entity_ids=(3,),
        )

        client.handle_datagram(pack_snapshot(full, seq=1, tick=10))
        client.handle_datagram(pack_snapshot(delta, seq=2, tick=13))

        latest = client.latest_snapshot
        self.assertEqual(latest.snapshot_id, 2)
        self.assertEqual([e.entity_id for e in latest.entities], [1, 2, 4])
        moved = next(e for e in latest.entities if e.entity_id == 2)
        self.assertEqual(moved.x, 250)
        self.assertEqual(client.deltas_received, 1)

    def test_delta_with_missing_base_is_ignored(self) -> None:
        client = GameClient(ClientConfig())
        delta = Snapshot(5, 1, (EntityState(1, 1, 2, 0, 0),))
        client.handle_datagram(pack_snapshot(delta, seq=1, tick=10))
        self.assertIsNone(client.latest_snapshot)
        self.assertIsNone(client.last_received_snapshot)


class C3IntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_c3_receives_deltas_and_moves(self) -> None:
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

            client = CompressedClient(
                ClientConfig(
                    host="127.0.0.1",
                    port=port,
                    input_rate=60,
                    player_slot=1,
                    redundancy=3,
                ),
                ServerConfig(tick_rate=60),
            )
            client_task = asyncio.create_task(client.run(duration=3.0))
            await asyncio.wait_for(client.joined.wait(), timeout=2.0)
            session = server.sessions_by_player[client.player_id]
            self.assertTrue(session.delta_enabled)

            client.set_input(1, 0)
            for _ in range(300):
                if client.latest_snapshot is not None and client.deltas_received > 0:
                    break
                await asyncio.sleep(0.01)

            self.assertIsNotNone(client.latest_snapshot)
            self.assertGreater(client.deltas_received, 0, "no delta snapshots received")
            entity = client.get_entity()
            self.assertIsNotNone(entity)
        finally:
            if client_task is not None:
                client_task.cancel()
                await asyncio.gather(client_task, return_exceptions=True)
            server_task.cancel()
            await asyncio.gather(server_task, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
