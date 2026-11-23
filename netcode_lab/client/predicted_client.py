"""C1 client: the C0 client plus client-side prediction and reconciliation.

Which input has the server processed?
-------------------------------------
Protocol v1 snapshots do not carry a "last processed input" field, and the
protocol is frozen, so this client derives it from the packet header instead:

* every outgoing INPUT packet has a packet ``seq``; the client remembers which
  ``input_seq`` each packet carried;
* every snapshot's header carries ``ack`` = the highest client packet ``seq``
  the server has received at the moment it built that snapshot;
* the server applies an INPUT as soon as the packet arrives and ignores older
  ``input_seq`` values, so the input inside packet ``ack`` is exactly the
  newest input the server has applied.

This stays correct for C3 input redundancy too, because the newest record in
each packet is still the one that ends up applied.
"""

from __future__ import annotations

import asyncio
import bisect
import logging
import time

from netcode_lab.client.client import ClientConfig, GameClient
from netcode_lab.client.prediction import Predictor, seq_newer
from netcode_lab.config import ServerConfig
from netcode_lab.protocol import messages as proto

log = logging.getLogger(__name__)

# How many sent-input records to keep while waiting for acknowledgement.
# At 30 inputs/s this covers several seconds of RTT.
MAX_TRACKED_INPUTS = 1024


class PredictedClient(GameClient):
    mode = "c1"

    def __init__(self, config: ClientConfig, server: ServerConfig | None = None) -> None:
        super().__init__(config)
        server = server or ServerConfig()
        tick_dt = server.tick_dt
        if abs(config.input_interval - tick_dt) > 1e-9:
            log.warning(
                "C1 predicts one server tick per input: input rate %.1f/s does not "
                "match server tick rate %.1f/s, expect extra corrections",
                config.input_rate,
                server.tick_rate,
            )
        self.predictor = Predictor(
            world_width=server.world_width,
            world_height=server.world_height,
            player_speed=server.player_speed,
            tick_dt=tick_dt,
        )
        # Parallel, strictly increasing lists: packet seq -> input seq.
        self._sent_packet_seqs: list[int] = []
        self._sent_input_seqs: list[int] = []
        self._last_snapshot_id = 0

    # -- what the game should draw ----------------------------------------------

    def get_entity(self) -> proto.EntityState | None:
        """The local player, as predicted (not as last reported by the server)."""
        if self.player_id is None or not self.predictor.initialized:
            return None
        p = self.predictor
        return proto.EntityState(
            entity_id=self.player_id,
            x=round(p.x),
            y=round(p.y),
            vx=round(p.vx),
            vy=round(p.vy),
        )

    def get_server_entity(self) -> proto.EntityState | None:
        """The local player exactly as the latest snapshot reported it (C0 view)."""
        return super().get_entity()

    def get_remote_entities(self) -> tuple[proto.EntityState, ...]:
        """Other players, straight from the latest snapshot (C2 will interpolate)."""
        snapshot = self.latest_snapshot
        if snapshot is None:
            return ()
        return tuple(e for e in snapshot.entities if e.entity_id != self.player_id)

    # -- sending --------------------------------------------------------------

    def _send_input(self) -> None:
        self.input_seq = (self.input_seq + 1) & 0xFFFFFFFF
        record = proto.InputRecord(
            input_seq=self.input_seq,
            tick=self.tick,
            move_x=self.move_x,
            move_y=self.move_y,
            buttons=self.buttons,
        )
        header = self._header(proto.MsgType.INPUT)
        self._send(proto.pack_input(header, (record,)))

        self._sent_packet_seqs.append(header.seq)
        self._sent_input_seqs.append(self.input_seq)
        if len(self._sent_packet_seqs) > MAX_TRACKED_INPUTS:
            del self._sent_packet_seqs[0]
            del self._sent_input_seqs[0]

        self.predictor.apply_local_input(self.input_seq, self.move_x, self.move_y)

    async def _input_loop(self) -> None:
        # Deadline-based so inputs go out at the true tick rate. The C0 loop
        # sleeps a fixed interval after each send, which drifts slightly slow;
        # for prediction that drift would show up as constant corrections.
        interval = self.config.input_interval
        next_deadline = time.monotonic()
        while self._running:
            if self.joined.is_set():
                self._send_input()
            next_deadline += interval
            now = time.monotonic()
            if next_deadline < now:
                next_deadline = now
            await asyncio.sleep(next_deadline - now)

    # -- receiving ------------------------------------------------------------

    def handle_datagram(self, data: bytes) -> None:
        received_before = self.snapshots_received
        super().handle_datagram(data)
        if self.snapshots_received == received_before:
            return  # not a (valid) snapshot

        snapshot = self.latest_snapshot
        if snapshot is None or self.player_id is None:
            return
        if self._last_snapshot_id and not seq_newer(snapshot.snapshot_id, self._last_snapshot_id):
            return  # late, out-of-order snapshot: older than one already used
        self._last_snapshot_id = snapshot.snapshot_id

        own = next((e for e in snapshot.entities if e.entity_id == self.player_id), None)
        if own is None:
            return

        header, _ = proto.decode(data)  # already validated by super()
        acked_input = self._input_for_packet_ack(header.ack)
        self.predictor.reconcile(own.x, own.y, acked_input, own.vx, own.vy)

    def _input_for_packet_ack(self, packet_ack: int) -> int:
        """Newest input_seq carried by a packet with seq <= packet_ack (0 if none)."""
        idx = bisect.bisect_right(self._sent_packet_seqs, packet_ack)
        if idx == 0:
            return 0
        acked = self._sent_input_seqs[idx - 1]
        # Forget records the server has definitely already seen.
        if idx > 1:
            del self._sent_packet_seqs[: idx - 1]
            del self._sent_input_seqs[: idx - 1]
        return acked
