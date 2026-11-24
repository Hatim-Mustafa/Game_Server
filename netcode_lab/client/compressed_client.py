from __future__ import annotations

from collections import deque

from netcode_lab.client.client import ClientConfig
from netcode_lab.client.interpolated_client import InterpolatedClient
from netcode_lab.config import ServerConfig
from netcode_lab.protocol import messages as proto


class CompressedClient(InterpolatedClient):
    """C3 client: C2 plus input redundancy and delta-compressed snapshots."""

    mode = "c3"

    def __init__(
        self,
        config: ClientConfig,
        server: ServerConfig | None = None,
        interpolation_delay: float = 0.1,
        max_extrapolation: float = 0.1,
    ) -> None:
        super().__init__(config, server, interpolation_delay, max_extrapolation)
        self.redundancy = max(1, int(config.redundancy))
        self._recent_inputs: deque[proto.InputRecord] = deque(maxlen=self.redundancy)
        self.capabilities |= int(proto.Capability.DELTA_SNAPSHOTS)

    def _inputs_to_send(
        self, record: proto.InputRecord
    ) -> tuple[proto.InputRecord, ...]:
        self._recent_inputs.append(record)
        return tuple(self._recent_inputs)
