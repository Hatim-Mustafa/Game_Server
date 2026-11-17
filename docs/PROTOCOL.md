# NetCode Lab — Wire Protocol (frozen v1)

Status: **frozen**. This document is the single source of truth for the binary
format. The reference implementation lives in
`netcode_lab/protocol/messages.py`. Any breaking change requires bumping
`VERSION` in that module and updating this document in the same commit.

- Transport: **UDP**, one game message per datagram. No message boundaries
  beyond the datagram itself.
- Byte order: **little-endian** for every multi-byte field (Python formats
  use `<`).
- Integers are **unsigned** unless marked signed.
- Sequence numbers start at **1**; the value `0` means "none / not yet seen".
  All sequence counters wrap modulo `2^32`.

## Header (22 bytes, present on every message)

| Offset | Size | Field        | Type   | Meaning |
|-------:|-----:|--------------|--------|---------|
| 0      | 4    | `protocol_id`| uint32 | Always `0x4E433031` (ASCII `NC01`) |
| 4      | 1    | `version`    | uint8  | Always `1` |
| 5      | 1    | `msg_type`   | uint8  | See message table |
| 6      | 4    | `seq`        | uint32 | Sender's packet sequence number |
| 10     | 4    | `ack`        | uint32 | Highest `seq` received from the peer |
| 14     | 4    | `ack_bits`   | uint32 | Bitmap of earlier packets received |
| 18     | 4    | `tick`       | uint32 | Sender's current tick |

Struct format: `<IBBIIII`.

`tick` is the server's simulation tick for server-to-client messages. For
client-to-server messages it is the most recent server tick the client has
observed (or `0` before the first snapshot).

A receiver MUST discard a datagram when: it is shorter than 22 bytes, the
`protocol_id` does not match, the `version` does not match, or `msg_type` is
not a known value.

## Acknowledgement semantics

`ack` and `ack_bits` together acknowledge up to 32 packet sequence numbers.

- `ack` is the highest sequence number received from the peer so far.
- `ack_bits` bit `i` (0 = least significant) acknowledges sequence number
  `ack - 1 - i`.
- The `ack` value itself is always implicitly acknowledged; its bit is not set
  in `ack_bits`.
- If nothing has been received yet, send `ack = 0` and `ack_bits = 0`.
- Only the most recent 32 numbers are representable; older ones are assumed
  received.

Example: received sequences 3, 4, 6. Then `ack = 6`,
`ack_bits = 0b1010` (bit 0 = seq 5 missing, bit 1 = seq 4, bit 2 = seq 3).

## Message types

| `msg_type` | Name       | Direction | Payload |
|-----------:|------------|-----------|---------|
| 1          | `JOIN`     | client → server | none |
| 2          | `JOIN_ACK` | server → client | `player_id` |
| 3          | `INPUT`    | client → server | input records |
| 4          | `SNAPSHOT` | server → client | snapshot |
| 5          | `EVENT`    | server → client | event |
| 6          | `LEAVE`    | client → server | none |

Senders must use the direction listed. Unexpected directions are ignored by
the receiver.

### 1. JOIN (client → server)

Header only. Requests admission to the game. On success the server replies
with `JOIN_ACK`; a client that does not receive one may resend `JOIN` with a
new `seq`.

### 2. JOIN_ACK (server → client)

| Offset | Size | Field       | Type  |
|-------:|-----:|-------------|-------|
| 22     | 1    | `player_id` | uint8 |

Struct: `<B`. Total packet size: 23 bytes.

`player_id` is also the entity id used in snapshots, and is always in
`1..255`.

### 3. INPUT (client → server)

| Offset | Size | Field         | Type  | Meaning |
|-------:|-----:|---------------|-------|---------|
| 22     | 1    | `count`       | uint8 | Number of records that follow (max 255) |
| 23     | ...  | `records[count]` | | Each record is 11 bytes |

Each input record (11 bytes, format `<IIbbB`):

| Rel. offset | Size | Field       | Type  | Meaning |
|------------:|-----:|-------------|-------|---------|
| 0           | 4    | `input_seq` | uint32 | Client's input sequence, starts at 1 |
| 4           | 4    | `tick`      | uint32 | Client's tick when the input was produced |
| 8           | 1    | `move_x`    | int8  | Horizontal intent, clamped to `-1..1` by server |
| 9           | 1    | `move_y`    | int8  | Vertical intent, clamped to `-1..1` by server |
| 10          | 1    | `buttons`   | uint8 | Bit flags, reserved |

The server ignores any record whose `input_seq` is not greater than the last
accepted `input_seq` for that player, so repeated (redundant) records are
safe. `count` may be `0`.

### 4. SNAPSHOT (server → client)

| Offset | Size | Field              | Type   | Meaning |
|-------:|-----:|--------------------|--------|---------|
| 22     | 4    | `snapshot_id`      | uint32 | Server snapshot counter, starts at 1 |
| 26     | 4    | `base_snapshot_id` | uint32 | `0` = full snapshot |
| 30     | 1    | `entity_count`     | uint8  | Number of entity records (max 255) |
| 31     | ...  | `entities[entity_count]` | | Each entity record is 9 bytes |

Each entity record (9 bytes, format `<Bhhhh`):

| Rel. offset | Size | Field       | Type  | Meaning |
|------------:|-----:|-------------|-------|---------|
| 0           | 1    | `entity_id` | uint8 | Player/entity id |
| 1           | 2    | `x`         | int16 | World x position (rounded) |
| 3           | 2    | `y`         | int16 | World y position (rounded) |
| 5           | 2    | `vx`        | int16 | x velocity (units/second, rounded) |
| 7           | 2    | `vy`        | int16 | y velocity (units/second, rounded) |

`base_snapshot_id = 0` means the payload is a complete state. Delta
compression (C3) will use a non-zero `base_snapshot_id` in a future protocol
version; v1 always sends `0`.

### 5. EVENT (server → client)

| Offset | Size | Field        | Type   | Meaning |
|-------:|-----:|--------------|--------|---------|
| 22     | 4    | `event_id`   | uint32 | Reliable-channel id |
| 26     | 1    | `event_type` | uint8  | Application-defined |
| 27     | ...  | `data`       | bytes  | Opaque, length = remainder |

Reserved for the reliable event channel. No `event_type` values are defined
in v1.

### 6. LEAVE (client → server)

Header only. Announces an intentional disconnect. Best-effort; the server
also removes a client on its own timeout policy (not implemented in v1).

## Worked example

`JOIN_ACK` assigning player 1, with `seq = 0`, `ack = 1`, `ack_bits = 0`,
`tick = 0`:

```
31 30 43 4E   protocol_id = "NC01"
01            version
02            msg_type = JOIN_ACK
00 00 00 00   seq = 0
01 00 00 00   ack = 1
00 00 00 00   ack_bits = 0
00 00 00 00   tick = 0
01            player_id = 1
```

## Known v1 simplifications

- The server's `seq` is a single global counter shared across all clients.
  As a result a client can observe gaps caused by packets sent to other
  clients. Acknowledgement logic remains correct, but per-client gap counting
  is not meaningful. This will be made per-session before measuring loss.
- No packet-level checksum beyond the UDP/IP checksums.
