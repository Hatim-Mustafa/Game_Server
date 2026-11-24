from __future__ import annotations

import asyncio
import unittest

from netcode_lab.client.client import ClientConfig, GameClient
from netcode_lab.config import ServerConfig
from netcode_lab.protocol import messages as proto
from netcode_lab.protocol.messages import Header, MsgType
from netcode_lab.server.server import GameServer, Session


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []

    def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
        self.sent.append((data, addr))


def event_ack_payload(ack: int, ack_bits: int = 0):
    packet = proto.pack_event_ack(Header(MsgType.EVENT_ACK, seq=1), ack, ack_bits)
    _, payload = proto.decode(packet)
    return payload


def make_server_with_session() -> tuple[GameServer, Session]:
    server = GameServer(ServerConfig(host="127.0.0.1", port=0))
    server.transport = FakeTransport()
    session = Session(player_id=1, addr=("127.0.0.1", 1234))
    server.sessions_by_addr[session.addr] = session
    server.sessions_by_player[1] = session
    return server, session


class ReliableEventTest(unittest.TestCase):
    def test_retransmits_until_acked(self) -> None:
        server, session = make_server_with_session()
        server._queue_event(session, proto.EventType.KO, b"\x01")

        self.assertEqual(len(session.pending_events), 1)
        self.assertEqual(len(server.transport.sent), 1)

        event = next(iter(session.pending_events.values()))
        server._retry_events(now=event.last_sent + 0.01)
        self.assertEqual(len(server.transport.sent), 1)

        server._retry_events(
            now=event.last_sent + server.config.event_retry_interval + 0.01
        )
        self.assertEqual(len(server.transport.sent), 2)

        server._on_event_ack(event_ack_payload(1), session)
        self.assertEqual(session.pending_events, {})

    def test_ack_bitfield_leaves_gaps_pending(self) -> None:
        server, session = make_server_with_session()
        server._queue_event(session, proto.EventType.KO, b"")
        server._queue_event(session, proto.EventType.KO, b"")
        server._queue_event(session, proto.EventType.KO, b"")

        server._on_event_ack(event_ack_payload(3, 0b010), session)

        self.assertEqual(set(session.pending_events), {2})

    def test_retry_gives_up_after_max_attempts(self) -> None:
        server, session = make_server_with_session()
        server._queue_event(session, proto.EventType.REMATCH, b"")
        event = next(iter(session.pending_events.values()))
        now = event.last_sent
        for _ in range(server.config.event_max_retries):
            now += server.config.event_retry_interval + 0.01
            server._retry_events(now=now)
        self.assertEqual(session.pending_events, {})


class ReliableEventIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_join_and_ko_events_are_delivered_and_acked(self) -> None:
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
            port = server.transport.get_extra_info("sockname")[1]

            client = GameClient(ClientConfig(host="127.0.0.1", port=port))
            client_task = asyncio.create_task(client.run(duration=3.0))
            await asyncio.wait_for(client.joined.wait(), timeout=2.0)

            joined = int(proto.EventType.PLAYER_JOINED)
            for _ in range(200):
                if any(event_type == joined for _, event_type, _ in client.events):
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(
                any(event_type == joined for _, event_type, _ in client.events)
            )

            server.world.entities[client.player_id].health = 0
            ko = int(proto.EventType.KO)
            for _ in range(300):
                if any(event_type == ko for _, event_type, _ in client.events):
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(
                any(event_type == ko for _, event_type, _ in client.events)
            )

            session = server.sessions_by_player[client.player_id]
            for _ in range(200):
                if not session.pending_events:
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(session.pending_events, {})
        finally:
            if client_task is not None:
                client_task.cancel()
                await asyncio.gather(client_task, return_exceptions=True)
            server_task.cancel()
            await asyncio.gather(server_task, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
