from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from netcode_lab.config import ServerConfig
from netcode_lab.protocol import messages as proto
from netcode_lab.protocol.ack import AckWindow
from netcode_lab.server.world import World

log = logging.getLogger(__name__)

Addr = tuple[str, int]


@dataclass
class Session:
    player_id: int
    addr: Addr
    ack_window: AckWindow = field(default_factory=AckWindow)


class GameServer:
    def __init__(self, config: ServerConfig) -> None:
        self.config = config
        self.world = World(config.world_width, config.world_height, config.player_speed)
        self.sessions_by_addr: dict[Addr, Session] = {}
        self.sessions_by_player: dict[int, Session] = {}
        self.transport: asyncio.DatagramTransport | None = None
        self.server_seq = 0
        self.tick = 0
        self.snapshot_id = 0
        self._next_player_id = 1
        self._running = False

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _ServerProtocol(self),
            local_addr=(self.config.host, self.config.port),
        )
        self.transport = transport
        actual = transport.get_extra_info("sockname")
        log.info("server listening on %s:%s", actual[0], actual[1])
        self._running = True
        try:
            await asyncio.gather(self._tick_loop(), self._snapshot_loop())
        finally:
            self._running = False
            transport.close()

    def handle_datagram(self, data: bytes, addr: Addr) -> None:
        try:
            header, payload = proto.decode(data)
        except proto.ProtocolError as exc:
            log.debug("dropping datagram from %s: %s", addr, exc)
            return

        session = self.sessions_by_addr.get(addr)
        if session is not None:
            session.ack_window.mark(header.seq)

        if header.msg_type is proto.MsgType.JOIN:
            self._on_join(addr)
        elif header.msg_type is proto.MsgType.INPUT:
            self._on_input(payload, session)
        elif header.msg_type is proto.MsgType.LEAVE:
            self._on_leave(addr)

    def _on_join(self, addr: Addr) -> None:
        existing = self.sessions_by_addr.get(addr)
        if existing is not None:
            self._send_join_ack(existing)
            return

        player_id = self._next_player_id
        self._next_player_id += 1
        x = self.config.world_width * (0.25 if player_id % 2 else 0.75)
        y = self.config.world_height * 0.5
        self.world.spawn(player_id, x, y)

        session = Session(player_id=player_id, addr=addr)
        self.sessions_by_addr[addr] = session
        self.sessions_by_player[player_id] = session
        log.info("player %d joined from %s", player_id, addr)
        self._send_join_ack(session)

    def _on_input(self, payload: memoryview, session: Session | None) -> None:
        if session is None:
            return
        try:
            records = proto.parse_input(payload)
        except proto.ProtocolError as exc:
            log.debug("bad INPUT from %s: %s", session.addr, exc)
            return
        for record in records:
            self.world.apply_input(
                session.player_id,
                record.input_seq,
                record.move_x,
                record.move_y,
                record.buttons,
            )

    def _on_leave(self, addr: Addr) -> None:
        session = self.sessions_by_addr.pop(addr, None)
        if session is None:
            return
        self.sessions_by_player.pop(session.player_id, None)
        self.world.remove(session.player_id)
        log.info("player %d left (%s)", session.player_id, addr)

    def _send_join_ack(self, session: Session) -> None:
        header = self._header(proto.MsgType.JOIN_ACK, session)
        packet = proto.pack_join_ack(header, session.player_id)
        self._send(session.addr, packet)

    def _header(self, msg_type: proto.MsgType, session: Session) -> proto.Header:
        ack, ack_bits = session.ack_window.build()
        header = proto.Header(
            msg_type=msg_type,
            seq=self._next_seq(),
            ack=ack,
            ack_bits=ack_bits,
            tick=self.tick,
        )
        return header

    def _next_seq(self) -> int:
        seq = self.server_seq
        self.server_seq = (self.server_seq + 1) & 0xFFFFFFFF
        return seq

    def _send(self, addr: Addr, packet: bytes) -> None:
        if self.transport is not None:
            self.transport.sendto(packet, addr)

    def _broadcast_snapshot(self) -> None:
        if not self.sessions_by_player:
            return
        self.snapshot_id += 1
        snapshot = proto.Snapshot(
            snapshot_id=self.snapshot_id,
            base_snapshot_id=0,
            entities=tuple(
                proto.EntityState(
                    entity_id=entity.entity_id,
                    x=round(entity.x),
                    y=round(entity.y),
                    vx=round(entity.vx),
                    vy=round(entity.vy),
                )
                for entity in self.world.entities.values()
            ),
        )
        payload = proto.snapshot_payload(snapshot)
        for session in self.sessions_by_player.values():
            header = self._header(proto.MsgType.SNAPSHOT, session)
            self._send(session.addr, proto.encode(header, payload))

    async def _tick_loop(self) -> None:
        dt = self.config.tick_dt
        next_deadline = time.monotonic()
        while self._running:
            self.tick += 1
            self.world.tick(dt)
            next_deadline += dt
            await asyncio.sleep(max(0.0, next_deadline - time.monotonic()))

    async def _snapshot_loop(self) -> None:
        interval = self.config.snapshot_interval
        next_deadline = time.monotonic() + interval
        while self._running:
            self._broadcast_snapshot()
            next_deadline += interval
            now = time.monotonic()
            if next_deadline < now:
                next_deadline = now + interval
            await asyncio.sleep(max(0.0, next_deadline - now))


class _ServerProtocol(asyncio.DatagramProtocol):
    def __init__(self, server: GameServer) -> None:
        self.server = server

    def datagram_received(self, data: bytes, addr: Addr) -> None:
        self.server.handle_datagram(data, addr)

    def error_received(self, exc: Exception) -> None:
        log.warning("datagram error: %s", exc)
