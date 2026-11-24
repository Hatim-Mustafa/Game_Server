from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum, IntFlag

PROTOCOL_ID = 0x4E433031
VERSION = 5
ACK_BITS_MASK = 0xFFFFFFFF

HEADER = struct.Struct("<IBBIIII")
HEADER_SIZE = HEADER.size

_JOIN = struct.Struct("<BB")
_JOIN_ACK = struct.Struct("<B")
_INPUT_COUNT = struct.Struct("<B")
_INPUT = struct.Struct("<IIbbB")
_SNAPSHOT_HEAD = struct.Struct("<IIBB")
_ENTITY = struct.Struct("<BhhhhBB")
_EVENT_HEAD = struct.Struct("<IB")
_EVENT_ACK = struct.Struct("<II")

FLAG_ATTACKING = 1 << 0
FLAG_DEAD = 1 << 1


class ProtocolError(ValueError):
    pass


class Button(IntFlag):
    ATTACK = 1 << 0
    REMATCH = 1 << 1


class Capability(IntFlag):
    DELTA_SNAPSHOTS = 1 << 0


class MsgType(IntEnum):
    JOIN = 1
    JOIN_ACK = 2
    INPUT = 3
    SNAPSHOT = 4
    EVENT = 5
    LEAVE = 6
    EVENT_ACK = 7


class EventType(IntEnum):
    PLAYER_JOINED = 1
    PLAYER_LEFT = 2
    KO = 3
    REMATCH = 4


@dataclass(frozen=True)
class Header:
    msg_type: MsgType
    seq: int
    ack: int = 0
    ack_bits: int = 0
    tick: int = 0


@dataclass(frozen=True)
class InputRecord:
    input_seq: int
    tick: int
    move_x: int
    move_y: int
    buttons: int = 0


@dataclass(frozen=True)
class EntityState:
    entity_id: int
    x: int
    y: int
    vx: int
    vy: int
    health: int = 100
    flags: int = 0


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: int
    base_snapshot_id: int
    entities: tuple[EntityState, ...]
    removed_entity_ids: tuple[int, ...] = ()


def _i8(value: int) -> int:
    return max(-128, min(127, int(value)))


def encode(header: Header, payload: bytes = b"") -> bytes:
    return HEADER.pack(
        PROTOCOL_ID,
        VERSION,
        int(header.msg_type),
        header.seq & ACK_BITS_MASK,
        header.ack & ACK_BITS_MASK,
        header.ack_bits & ACK_BITS_MASK,
        header.tick & ACK_BITS_MASK,
    ) + payload


def decode(data: bytes) -> tuple[Header, memoryview]:
    if len(data) < HEADER_SIZE:
        raise ProtocolError(f"packet shorter than header ({len(data)} < {HEADER_SIZE})")
    protocol_id, version, msg_type_raw, seq, ack, ack_bits, tick = HEADER.unpack_from(data, 0)
    if protocol_id != PROTOCOL_ID:
        raise ProtocolError(f"bad protocol id 0x{protocol_id:08X}")
    if version != VERSION:
        raise ProtocolError(f"unsupported protocol version {version}")
    try:
        msg_type = MsgType(msg_type_raw)
    except ValueError as exc:
        raise ProtocolError(f"unknown message type {msg_type_raw}") from exc
    return Header(msg_type, seq, ack, ack_bits, tick), memoryview(data)[HEADER_SIZE:]


def pack_join(
    header: Header,
    desired_player_id: int = 0,
    capabilities: int = 0,
) -> bytes:
    return encode(
        header,
        _JOIN.pack(desired_player_id & 0xFF, capabilities & 0xFF),
    )


def parse_join(payload: memoryview) -> tuple[int, int]:
    if len(payload) < _JOIN.size:
        raise ProtocolError("short JOIN payload")
    desired_player_id, capabilities = _JOIN.unpack_from(payload, 0)
    return desired_player_id, capabilities


def pack_join_ack(header: Header, player_id: int) -> bytes:
    return encode(header, _JOIN_ACK.pack(player_id & 0xFF))


def parse_join_ack(payload: memoryview) -> int:
    if len(payload) < _JOIN_ACK.size:
        raise ProtocolError("short JOIN_ACK payload")
    return _JOIN_ACK.unpack_from(payload, 0)[0]


def pack_input(header: Header, inputs: tuple[InputRecord, ...] | list[InputRecord]) -> bytes:
    if len(inputs) > 255:
        raise ProtocolError("too many input records in one packet")
    body = bytearray(_INPUT_COUNT.pack(len(inputs)))
    for record in inputs:
        body += _INPUT.pack(
            record.input_seq & ACK_BITS_MASK,
            record.tick & ACK_BITS_MASK,
            _i8(record.move_x),
            _i8(record.move_y),
            record.buttons & 0xFF,
        )
    return encode(header, bytes(body))


def parse_input(payload: memoryview) -> tuple[InputRecord, ...]:
    if len(payload) < _INPUT_COUNT.size:
        raise ProtocolError("short INPUT payload")
    count = _INPUT_COUNT.unpack_from(payload, 0)[0]
    needed = _INPUT_COUNT.size + count * _INPUT.size
    if len(payload) < needed:
        raise ProtocolError("truncated INPUT payload")
    offset = _INPUT_COUNT.size
    records = []
    for _ in range(count):
        input_seq, tick, move_x, move_y, buttons = _INPUT.unpack_from(payload, offset)
        offset += _INPUT.size
        records.append(InputRecord(input_seq, tick, move_x, move_y, buttons))
    return tuple(records)


def snapshot_payload(snapshot: Snapshot) -> bytes:
    if len(snapshot.entities) > 255:
        raise ProtocolError("too many entities in one snapshot")
    if len(snapshot.removed_entity_ids) > 255:
        raise ProtocolError("too many removed entities in one snapshot")
    body = bytearray(
        _SNAPSHOT_HEAD.pack(
            snapshot.snapshot_id & ACK_BITS_MASK,
            snapshot.base_snapshot_id & ACK_BITS_MASK,
            len(snapshot.entities),
            len(snapshot.removed_entity_ids),
        )
    )
    for entity in snapshot.entities:
        body += _ENTITY.pack(
            entity.entity_id & 0xFF,
            max(-32768, min(32767, int(entity.x))),
            max(-32768, min(32767, int(entity.y))),
            max(-32768, min(32767, int(entity.vx))),
            max(-32768, min(32767, int(entity.vy))),
            max(0, min(255, int(entity.health))),
            entity.flags & 0xFF,
        )
    body += bytes(entity_id & 0xFF for entity_id in snapshot.removed_entity_ids)
    return bytes(body)


def pack_snapshot(header: Header, snapshot: Snapshot) -> bytes:
    return encode(header, snapshot_payload(snapshot))


def parse_snapshot(payload: memoryview) -> Snapshot:
    if len(payload) < _SNAPSHOT_HEAD.size:
        raise ProtocolError("short SNAPSHOT payload")
    snapshot_id, base_snapshot_id, count, removed_count = _SNAPSHOT_HEAD.unpack_from(
        payload, 0
    )
    needed = _SNAPSHOT_HEAD.size + count * _ENTITY.size + removed_count
    if len(payload) < needed:
        raise ProtocolError("truncated SNAPSHOT payload")
    offset = _SNAPSHOT_HEAD.size
    entities = []
    for _ in range(count):
        entity_id, x, y, vx, vy, health, flags = _ENTITY.unpack_from(payload, offset)
        offset += _ENTITY.size
        entities.append(EntityState(entity_id, x, y, vx, vy, health, flags))
    removed = tuple(payload[offset : offset + removed_count])
    return Snapshot(snapshot_id, base_snapshot_id, tuple(entities), removed)


def pack_event(header: Header, event_id: int, event_type: int, data: bytes = b"") -> bytes:
    return encode(header, _EVENT_HEAD.pack(event_id & ACK_BITS_MASK, event_type & 0xFF) + data)


def parse_event(payload: memoryview) -> tuple[int, int, bytes]:
    if len(payload) < _EVENT_HEAD.size:
        raise ProtocolError("short EVENT payload")
    event_id, event_type = _EVENT_HEAD.unpack_from(payload, 0)
    return event_id, event_type, bytes(payload[_EVENT_HEAD.size:])


def pack_event_ack(header: Header, ack: int, ack_bits: int) -> bytes:
    return encode(
        header,
        _EVENT_ACK.pack(ack & ACK_BITS_MASK, ack_bits & ACK_BITS_MASK),
    )


def parse_event_ack(payload: memoryview) -> tuple[int, int]:
    if len(payload) < _EVENT_ACK.size:
        raise ProtocolError("short EVENT_ACK payload")
    return _EVENT_ACK.unpack_from(payload, 0)


def pack_leave(header: Header) -> bytes:
    return encode(header)
