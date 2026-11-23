from __future__ import annotations

import unittest

from netcode_lab.client.client import ClientConfig
from netcode_lab.client.interpolated_client import InterpolatedClient
from netcode_lab.client.interpolation import SnapshotInterpolator
from netcode_lab.config import ServerConfig
from netcode_lab.protocol import messages as proto
from netcode_lab.protocol.messages import EntityState, Header, MsgType, Snapshot


def snapshot(
    snapshot_id: int,
    remote_x: int,
    remote_vx: int = 0,
    remote_health: int = 100,
    remote_flags: int = 0,
) -> Snapshot:
    return Snapshot(
        snapshot_id=snapshot_id,
        base_snapshot_id=0,
        entities=(
            EntityState(1, 100, 100, 0, 0),
            EntityState(2, remote_x, 100, remote_vx, 0, remote_health, remote_flags),
        ),
    )


class SnapshotInterpolatorTest(unittest.TestCase):
    def test_interpolates_between_snapshots_on_delayed_timeline(self) -> None:
        interpolator = SnapshotInterpolator(tick_rate=30, interpolation_delay=0.05)
        interpolator.add_snapshot(snapshot(1, 10), server_tick=100, received_at=1.0)
        interpolator.add_snapshot(snapshot(2, 20), server_tick=103, received_at=1.1)

        remote = interpolator.sample(now=1.1)[1]

        self.assertEqual(remote.entity_id, 2)
        self.assertEqual(remote.x, 15)

    def test_interpolates_across_a_lost_snapshot(self) -> None:
        interpolator = SnapshotInterpolator(
            tick_rate=30,
            interpolation_delay=2 / 30,
        )
        interpolator.add_snapshot(snapshot(1, 10), server_tick=100, received_at=1.0)
        interpolator.add_snapshot(snapshot(3, 30), server_tick=104, received_at=1.1)

        remote = interpolator.sample(now=1.1)[1]

        self.assertEqual(remote.x, 20)

    def test_extrapolation_is_bounded_and_world_clamped(self) -> None:
        interpolator = SnapshotInterpolator(
            tick_rate=30,
            interpolation_delay=0,
            max_extrapolation=0.05,
            world_width=100,
            world_height=100,
        )
        interpolator.add_snapshot(
            snapshot(1, 98, remote_vx=100), server_tick=10, received_at=1.0
        )

        remote = interpolator.sample(now=2.0)[1]

        self.assertEqual(remote.x, 100)

    def test_server_tick_wraparound_preserves_snapshot_order(self) -> None:
        interpolator = SnapshotInterpolator(tick_rate=30, interpolation_delay=0.05)
        interpolator.add_snapshot(
            snapshot(1, 10), server_tick=0xFFFFFFFE, received_at=1.0
        )
        interpolator.add_snapshot(snapshot(2, 20), server_tick=1, received_at=1.1)

        remote = interpolator.sample(now=1.1)[1]

        self.assertEqual(remote.x, 15)

    def test_rejects_invalid_timing_configuration(self) -> None:
        with self.assertRaises(ValueError):
            SnapshotInterpolator(tick_rate=0)

    def test_c2_client_interpolates_remote_but_keeps_local_entity(self) -> None:
        client = InterpolatedClient(
            ClientConfig(input_rate=30),
            ServerConfig(tick_rate=30),
            interpolation_delay=0.05,
        )
        client.player_id = 1
        first = proto.pack_snapshot(
            Header(MsgType.SNAPSHOT, seq=10, tick=100),
            snapshot(1, 10),
        )
        second = proto.pack_snapshot(
            Header(MsgType.SNAPSHOT, seq=11, tick=103),
            snapshot(2, 20, remote_health=70, remote_flags=proto.FLAG_ATTACKING),
        )

        client.handle_datagram(first)
        client.handle_datagram(second)

        remote = client.get_remote_entities()
        self.assertEqual(len(remote), 1)
        self.assertEqual(remote[0].entity_id, 2)
        self.assertEqual(remote[0].x, 15)
        self.assertEqual(remote[0].health, 70)
        self.assertEqual(remote[0].flags, proto.FLAG_ATTACKING)
        self.assertIsNotNone(client.get_entity())


if __name__ == "__main__":
    unittest.main()
