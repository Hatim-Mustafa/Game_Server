from __future__ import annotations

import unittest

from netcode_lab.protocol import messages as proto
from netcode_lab.protocol.ack import AckWindow


class HeaderRoundTripTest(unittest.TestCase):
    def test_encode_decode_header(self) -> None:
        header = proto.Header(proto.MsgType.INPUT, seq=7, ack=5, ack_bits=0b101, tick=42)
        decoded, payload = proto.decode(proto.encode(header))
        self.assertEqual(decoded, header)
        self.assertEqual(len(payload), 0)

    def test_bad_protocol_id(self) -> None:
        packet = bytearray(proto.encode(proto.Header(proto.MsgType.JOIN, seq=1)))
        packet[0] = 0x00
        with self.assertRaises(proto.ProtocolError):
            proto.decode(bytes(packet))

    def test_short_packet(self) -> None:
        with self.assertRaises(proto.ProtocolError):
            proto.decode(b"\x01\x02")


class JoinAckTest(unittest.TestCase):
    def test_round_trip(self) -> None:
        header = proto.Header(proto.MsgType.JOIN_ACK, seq=1)
        decoded, payload = proto.decode(proto.pack_join_ack(header, 3))
        self.assertEqual(decoded.msg_type, proto.MsgType.JOIN_ACK)
        self.assertEqual(proto.parse_join_ack(payload), 3)


class InputTest(unittest.TestCase):
    def test_round_trip_with_redundancy(self) -> None:
        header = proto.Header(proto.MsgType.INPUT, seq=11)
        records = (
            proto.InputRecord(input_seq=39, tick=100, move_x=1, move_y=0, buttons=1),
            proto.InputRecord(input_seq=40, tick=101, move_x=1, move_y=-1, buttons=0),
            proto.InputRecord(input_seq=41, tick=102, move_x=-1, move_y=0, buttons=2),
        )
        decoded, payload = proto.decode(proto.pack_input(header, records))
        self.assertEqual(decoded.msg_type, proto.MsgType.INPUT)
        self.assertEqual(proto.parse_input(payload), records)

    def test_empty_input_list(self) -> None:
        header = proto.Header(proto.MsgType.INPUT, seq=1)
        _, payload = proto.decode(proto.pack_input(header, ()))
        self.assertEqual(proto.parse_input(payload), ())

    def test_truncated_payload(self) -> None:
        header = proto.Header(proto.MsgType.INPUT, seq=1)
        packet = proto.pack_input(header, (proto.InputRecord(1, 1, 1, 0),))
        _, payload = proto.decode(packet)
        with self.assertRaises(proto.ProtocolError):
            proto.parse_input(payload[:-1])


class SnapshotTest(unittest.TestCase):
    def test_round_trip(self) -> None:
        snapshot = proto.Snapshot(
            snapshot_id=12,
            base_snapshot_id=0,
            entities=(
                proto.EntityState(1, 100, 200, 0, -200),
                proto.EntityState(2, -5, 3, 10, 10),
            ),
        )
        header = proto.Header(proto.MsgType.SNAPSHOT, seq=210)
        decoded, payload = proto.decode(proto.pack_snapshot(header, snapshot))
        self.assertEqual(decoded.msg_type, proto.MsgType.SNAPSHOT)
        self.assertEqual(proto.parse_snapshot(payload), snapshot)


class AckWindowTest(unittest.TestCase):
    def test_out_of_order_marks(self) -> None:
        window = AckWindow()
        window.mark(5)
        self.assertEqual(window.build(), (5, 0))
        window.mark(4)
        window.mark(3)
        self.assertEqual(window.build(), (5, 0b11))
        window.mark(6)
        self.assertEqual(window.build(), (6, 0b111))
        window.mark(2)
        self.assertEqual(window.build(), (6, 0b1111))

    def test_zero_is_ignored(self) -> None:
        window = AckWindow()
        window.mark(0)
        self.assertEqual(window.build(), (0, 0))


if __name__ == "__main__":
    unittest.main()
