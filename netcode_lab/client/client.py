from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from netcode_lab.protocol import messages as proto
from netcode_lab.protocol.ack import AckWindow

log = logging.getLogger(__name__)

Addr = tuple[str, int]


@dataclass(frozen=True)
class ClientConfig:
    host: str = "127.0.0.1"
    port: int = 9000
    input_rate: float = 30.0
    join_retry_interval: float = 0.5
    player_slot: int = 0

    @property
    def input_interval(self) -> float:
        return 1.0 / self.input_rate


class GameClient:
    def __init__(self, config: ClientConfig) -> None:
        self.config = config
        self.server_addr: Addr = (config.host, config.port)
        self.transport: asyncio.DatagramTransport | None = None
        self.desired_player_id = config.player_slot & 0xFF
        self.rejected = False
        self.player_id: int | None = None
        self.server_ack = AckWindow()
        self.local_seq = 0
        self.input_seq = 0
        self.tick = 0
        self.latest_snapshot: proto.Snapshot | None = None
        self.snapshots_received = 0
        self.joined = asyncio.Event()
        self.move_x = 0
        self.move_y = 0
        self.buttons = 0
        self._running = False
        self._tasks: list[asyncio.Task] = []

    async def run(self, duration: float | None = None) -> None:
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _ClientProtocol(self),
            local_addr=("0.0.0.0", 0),
        )
        self.transport = transport
        self._running = True
        self._tasks = [
            asyncio.create_task(self._join_loop()),
            asyncio.create_task(self._input_loop()),
        ]
        try:
            if duration is None:
                await asyncio.Event().wait()
            else:
                await asyncio.sleep(duration)
        finally:
            self._running = False
            for task in self._tasks:
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._send_leave()
            transport.close()

    def set_input(self, move_x: int, move_y: int, buttons: int = 0) -> None:
        self.move_x = max(-1, min(1, int(move_x)))
        self.move_y = max(-1, min(1, int(move_y)))
        self.buttons = int(buttons) & 0xFF

    def get_entity(self) -> proto.EntityState | None:
        snapshot = self.latest_snapshot
        if snapshot is None or self.player_id is None:
            return None
        return next((e for e in snapshot.entities if e.entity_id == self.player_id), None)

    def handle_datagram(self, data: bytes) -> None:
        try:
            header, payload = proto.decode(data)
        except proto.ProtocolError as exc:
            log.debug("dropping datagram: %s", exc)
            return

        self.server_ack.mark(header.seq)
        self.tick = header.tick

        try:
            if header.msg_type is proto.MsgType.JOIN_ACK:
                assigned = proto.parse_join_ack(payload)
                if assigned == 0 or (
                    self.desired_player_id and assigned != self.desired_player_id
                ):
                    self.rejected = True
                    log.warning(
                        "server denied player slot %d (already in use)",
                        self.desired_player_id,
                    )
                    return
                self.player_id = assigned
                if not self.joined.is_set():
                    log.info("joined as player %d", self.player_id)
                    self.joined.set()
            elif header.msg_type is proto.MsgType.SNAPSHOT:
                self.latest_snapshot = proto.parse_snapshot(payload)
                self.snapshots_received += 1
            elif header.msg_type is proto.MsgType.EVENT:
                event_id, event_type, _ = proto.parse_event(payload)
                log.info("event %d type %d", event_id, event_type)
        except proto.ProtocolError as exc:
            log.debug("bad payload: %s", exc)

    def _next_seq(self) -> int:
        seq = self.local_seq
        self.local_seq = (self.local_seq + 1) & 0xFFFFFFFF
        return seq

    def _header(self, msg_type: proto.MsgType) -> proto.Header:
        ack, ack_bits = self.server_ack.build()
        return proto.Header(
            msg_type=msg_type,
            seq=self._next_seq(),
            ack=ack,
            ack_bits=ack_bits,
            tick=self.tick,
        )

    def _send(self, packet: bytes) -> None:
        if self.transport is not None:
            self.transport.sendto(packet, self.server_addr)

    def _send_join(self) -> None:
        self._send(
            proto.pack_join(self._header(proto.MsgType.JOIN), self.desired_player_id)
        )

    def _send_leave(self) -> None:
        if self.transport is not None:
            self._send(proto.pack_leave(self._header(proto.MsgType.LEAVE)))

    def _send_input(self) -> None:
        self.input_seq = (self.input_seq + 1) & 0xFFFFFFFF
        record = proto.InputRecord(
            input_seq=self.input_seq,
            tick=self.tick,
            move_x=self.move_x,
            move_y=self.move_y,
            buttons=self.buttons,
        )
        self._send(proto.pack_input(self._header(proto.MsgType.INPUT), (record,)))

    async def _join_loop(self) -> None:
        while self._running:
            if self.joined.is_set() or self.rejected:
                return
            self._send_join()
            try:
                await asyncio.wait_for(self.joined.wait(), timeout=self.config.join_retry_interval)
            except asyncio.TimeoutError:
                continue

    async def _input_loop(self) -> None:
        while self._running:
            if self.joined.is_set():
                self._send_input()
            await asyncio.sleep(self.config.input_interval)


class _ClientProtocol(asyncio.DatagramProtocol):
    def __init__(self, client: GameClient) -> None:
        self.client = client

    def datagram_received(self, data: bytes, addr: Addr) -> None:
        self.client.handle_datagram(data)

    def error_received(self, exc: Exception) -> None:
        log.warning("datagram error: %s", exc)
