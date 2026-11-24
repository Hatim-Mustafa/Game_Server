from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from netcode_lab.config import ServerConfig
from netcode_lab.protocol import messages as proto
from netcode_lab.protocol.ack import AckWindow, seq_newer
from netcode_lab.server.world import World

log = logging.getLogger(__name__)

Addr = tuple[str, int]
SNAPSHOT_HISTORY_SIZE = 128
SENT_SEQ_HISTORY_SIZE = 256


@dataclass
class PendingEvent:
    event_id: int
    event_type: int
    data: bytes
    last_sent: float
    attempts: int


@dataclass
class Session:
    player_id: int
    addr: Addr
    ack_window: AckWindow = field(default_factory=AckWindow)
    capabilities: int = 0
    delta_enabled: bool = False
    acked_snapshot_id: int = 0
    sent_snapshots: dict[int, int] = field(default_factory=dict)
    next_event_id: int = 1
    pending_events: dict[int, PendingEvent] = field(default_factory=dict)


class GameServer:
    def __init__(self, config: ServerConfig) -> None:
        self.config = config
        self.world = World(
            config.world_width,
            config.world_height,
            config.player_speed,
            max_health=config.max_health,
            attack_damage=config.attack_damage,
            attack_range=config.attack_range,
            attack_cooldown=config.attack_cooldown,
            attack_duration=config.attack_duration,
        )
        self.sessions_by_addr: dict[Addr, Session] = {}
        self.sessions_by_player: dict[int, Session] = {}
        self.transport: asyncio.DatagramTransport | None = None
        self.server_seq = 0
        self.tick = 0
        self.snapshot_id = 0
        self.snapshot_history: dict[int, dict[int, proto.EntityState]] = {}
        self._snapshot_history_order: list[int] = []
        self._prev_health: dict[int, int] = {}
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
            self._update_acked_snapshot(session, header.ack)

        if header.msg_type is proto.MsgType.JOIN:
            self._on_join(addr, payload)
        elif header.msg_type is proto.MsgType.INPUT:
            self._on_input(payload, session)
        elif header.msg_type is proto.MsgType.EVENT_ACK:
            self._on_event_ack(payload, session)
        elif header.msg_type is proto.MsgType.LEAVE:
            self._on_leave(addr)

    def _on_join(self, addr: Addr, payload: memoryview) -> None:
        existing = self.sessions_by_addr.get(addr)
        if existing is not None:
            self._send_join_ack(existing)
            return

        try:
            desired, capabilities = proto.parse_join(payload)
        except proto.ProtocolError as exc:
            log.debug("bad JOIN from %s: %s", addr, exc)
            return

        if desired and desired in self.sessions_by_player:
            log.info("player slot %d already taken; denying %s", desired, addr)
            self._send_join_denied(addr)
            return

        player_id = self._allocate_player_id(desired)
        if player_id is None:
            self._send_join_denied(addr)
            return
        x = self.config.world_width * (0.25 if player_id % 2 else 0.75)
        y = self.config.world_height * 0.5
        self.world.spawn(player_id, x, y)

        session = Session(
            player_id=player_id,
            addr=addr,
            capabilities=capabilities,
            delta_enabled=bool(capabilities & proto.Capability.DELTA_SNAPSHOTS),
        )
        self.sessions_by_addr[addr] = session
        self.sessions_by_player[player_id] = session
        log.info(
            "player %d joined from %s (delta=%s)",
            player_id,
            addr,
            session.delta_enabled,
        )
        self._send_join_ack(session)
        self._prev_health[player_id] = self.world.entities[player_id].health
        self._broadcast_event(proto.EventType.PLAYER_JOINED, bytes([player_id]))

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
        self._prev_health.pop(session.player_id, None)
        log.info("player %d left (%s)", session.player_id, addr)
        self._broadcast_event(proto.EventType.PLAYER_LEFT, bytes([session.player_id]))

    def _broadcast_event(self, event_type: int, data: bytes = b"") -> None:
        for session in self.sessions_by_player.values():
            self._queue_event(session, event_type, data)

    def _queue_event(self, session: Session, event_type: int, data: bytes) -> None:
        now = time.monotonic()
        event = PendingEvent(
            event_id=session.next_event_id,
            event_type=int(event_type),
            data=data,
            last_sent=now,
            attempts=0,
        )
        session.next_event_id += 1
        session.pending_events[event.event_id] = event
        self._send_event(session, event, now)

    def _send_event(self, session: Session, event: PendingEvent, now: float) -> None:
        header = self._header(proto.MsgType.EVENT, session)
        packet = proto.pack_event(
            header, event.event_id, event.event_type, event.data
        )
        self._send(session.addr, packet)
        event.last_sent = now
        event.attempts += 1

    def _retry_events(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        interval = self.config.event_retry_interval
        for session in self.sessions_by_player.values():
            for event_id in list(session.pending_events):
                event = session.pending_events[event_id]
                if now - event.last_sent < interval:
                    continue
                if event.attempts >= self.config.event_max_retries:
                    del session.pending_events[event_id]
                    log.warning(
                        "dropping event %d for player %d after %d attempts",
                        event_id,
                        session.player_id,
                        event.attempts,
                    )
                    continue
                self._send_event(session, event, now)

    def _on_event_ack(self, payload: memoryview, session: Session | None) -> None:
        if session is None:
            return
        try:
            ack, ack_bits = proto.parse_event_ack(payload)
        except proto.ProtocolError as exc:
            log.debug("bad EVENT_ACK from %s: %s", session.addr, exc)
            return
        if ack == 0:
            return
        for event_id in list(session.pending_events):
            offset = ack - event_id
            acknowledged = offset == 0 or (
                1 <= offset <= 32 and bool(ack_bits & (1 << (offset - 1)))
            )
            if acknowledged or offset >= 32:
                del session.pending_events[event_id]

    def _detect_events(self) -> None:
        for player_id, entity in self.world.entities.items():
            previous = self._prev_health.get(player_id, entity.health)
            if previous > 0 and entity.health <= 0:
                self._broadcast_event(proto.EventType.KO, bytes([player_id]))
            self._prev_health[player_id] = entity.health

    def _allocate_player_id(self, desired: int) -> int | None:
        if desired:
            return desired
        player_id = self._next_player_id
        while player_id in self.sessions_by_player and player_id <= 0xFF:
            player_id += 1
        if player_id > 0xFF:
            return None
        self._next_player_id = player_id + 1
        return player_id

    def _send_join_ack(self, session: Session) -> None:
        header = self._header(proto.MsgType.JOIN_ACK, session)
        packet = proto.pack_join_ack(header, session.player_id)
        self._send(session.addr, packet)

    def _send_join_denied(self, addr: Addr) -> None:
        header = proto.Header(
            msg_type=proto.MsgType.JOIN_ACK,
            seq=self._next_seq(),
            ack=0,
            ack_bits=0,
            tick=self.tick,
        )
        self._send(addr, proto.pack_join_ack(header, 0))

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
        states = self._world_states()
        self.snapshot_history[self.snapshot_id] = states
        self._snapshot_history_order.append(self.snapshot_id)
        while len(self._snapshot_history_order) > SNAPSHOT_HISTORY_SIZE:
            old = self._snapshot_history_order.pop(0)
            self.snapshot_history.pop(old, None)

        full_snapshot = proto.Snapshot(
            snapshot_id=self.snapshot_id,
            base_snapshot_id=0,
            entities=tuple(states[entity_id] for entity_id in sorted(states)),
        )
        full_payload = proto.snapshot_payload(full_snapshot)
        interval = self.config.delta_keyframe_interval
        force_keyframe = interval > 0 and self.snapshot_id % interval == 0

        for session in self.sessions_by_player.values():
            header = self._header(proto.MsgType.SNAPSHOT, session)
            if session.delta_enabled and not force_keyframe:
                payload = self._delta_payload(session, states, full_payload)
            else:
                payload = full_payload
            self._send(session.addr, proto.encode(header, payload))
            session.sent_snapshots[header.seq] = self.snapshot_id
            while len(session.sent_snapshots) > SENT_SEQ_HISTORY_SIZE:
                oldest = next(iter(session.sent_snapshots))
                del session.sent_snapshots[oldest]

    def _world_states(self) -> dict[int, proto.EntityState]:
        states: dict[int, proto.EntityState] = {}
        for entity in self.world.entities.values():
            flags = 0
            if entity.attack_active > 0.0:
                flags |= proto.FLAG_ATTACKING
            if entity.health <= 0:
                flags |= proto.FLAG_DEAD
            states[entity.entity_id] = proto.EntityState(
                entity_id=entity.entity_id,
                x=round(entity.x),
                y=round(entity.y),
                vx=round(entity.vx),
                vy=round(entity.vy),
                health=entity.health,
                flags=flags,
            )
        return states

    def _delta_payload(
        self,
        session: Session,
        states: dict[int, proto.EntityState],
        full_payload: bytes,
    ) -> bytes:
        base_id = session.acked_snapshot_id
        baseline = self.snapshot_history.get(base_id) if base_id else None
        if baseline is None:
            return full_payload
        changed = [
            states[entity_id]
            for entity_id in sorted(states)
            if baseline.get(entity_id) != states[entity_id]
        ]
        removed = tuple(
            entity_id for entity_id in sorted(baseline) if entity_id not in states
        )
        delta = proto.Snapshot(
            snapshot_id=self.snapshot_id,
            base_snapshot_id=base_id,
            entities=tuple(changed),
            removed_entity_ids=removed,
        )
        return proto.snapshot_payload(delta)

    def _update_acked_snapshot(self, session: Session, ack: int) -> None:
        if ack == 0:
            return
        snapshot_id = session.sent_snapshots.get(ack)
        if snapshot_id is None:
            return
        if session.acked_snapshot_id == 0 or seq_newer(
            snapshot_id, session.acked_snapshot_id
        ):
            session.acked_snapshot_id = snapshot_id

    async def _tick_loop(self) -> None:
        dt = self.config.tick_dt
        next_deadline = time.monotonic()
        while self._running:
            self.tick += 1
            self.world.tick(dt)
            if self.world.rematch_requested:
                if self.world.game_over:
                    self.world.rematch()
                    for player_id, entity in self.world.entities.items():
                        self._prev_health[player_id] = entity.health
                    self._broadcast_event(proto.EventType.REMATCH, b"")
                    log.info("rematch")
                else:
                    self.world.rematch_requested = False
            self._detect_events()
            next_deadline += dt
            await asyncio.sleep(max(0.0, next_deadline - time.monotonic()))

    async def _snapshot_loop(self) -> None:
        interval = self.config.snapshot_interval
        next_deadline = time.monotonic() + interval
        while self._running:
            self._broadcast_snapshot()
            self._retry_events()
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
