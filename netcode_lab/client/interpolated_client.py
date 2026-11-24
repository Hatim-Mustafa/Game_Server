from __future__ import annotations

import time

from netcode_lab.client.client import ClientConfig
from netcode_lab.client.interpolation import SnapshotInterpolator
from netcode_lab.client.predicted_client import PredictedClient
from netcode_lab.config import ServerConfig
from netcode_lab.protocol import messages as proto


class InterpolatedClient(PredictedClient):
    """C2 client: C1 prediction locally, delayed interpolation for remote players."""

    mode = "c2"

    def __init__(
        self,
        config: ClientConfig,
        server: ServerConfig | None = None,
        interpolation_delay: float = 0.1,
        max_extrapolation: float = 0.1,
    ) -> None:
        server_config = server or ServerConfig()
        super().__init__(config, server_config)
        self.interpolator = SnapshotInterpolator(
            tick_rate=server_config.tick_rate,
            interpolation_delay=interpolation_delay,
            max_extrapolation=max_extrapolation,
            world_width=server_config.world_width,
            world_height=server_config.world_height,
        )

    def handle_datagram(self, data: bytes) -> None:
        try:
            header, _ = proto.decode(data)
        except proto.ProtocolError:
            super().handle_datagram(data)
            return

        previous_count = self.snapshots_received
        received_at = time.monotonic()
        super().handle_datagram(data)
        if (
            header.msg_type is proto.MsgType.SNAPSHOT
            and self.snapshots_received != previous_count
            and self.last_received_snapshot is not None
        ):
            self.interpolator.add_snapshot(
                self.last_received_snapshot,
                server_tick=header.tick,
                received_at=received_at,
            )

    def get_remote_entities(self) -> tuple[proto.EntityState, ...]:
        sampled = self.interpolator.sample()
        latest = self.latest_snapshot
        if latest is None:
            return ()

        authoritative = {entity.entity_id: entity for entity in latest.entities}
        remote_entities = []
        for entity in sampled:
            if entity.entity_id == self.player_id:
                continue
            current = authoritative.get(entity.entity_id)
            if current is None:
                remote_entities.append(entity)
                continue
            remote_entities.append(
                proto.EntityState(
                    entity_id=entity.entity_id,
                    x=entity.x,
                    y=entity.y,
                    vx=entity.vx,
                    vy=entity.vy,
                    health=current.health,
                    flags=current.flags,
                )
            )
        return tuple(remote_entities)
