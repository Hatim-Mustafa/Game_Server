# NetCode Lab — Wire Protocol (v5)

Status: **frozen at v5**. This document is the single source of truth for the binary
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
| 4      | 1    | `version`    | uint8  | Always `5` |
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
| 1          | `JOIN`     | client → server | desired player id + capabilities |
| 2          | `JOIN_ACK` | server → client | assigned player id (`0` = denied) |
| 3          | `INPUT`    | client → server | input records |
| 4          | `SNAPSHOT` | server → client | snapshot |
| 5          | `EVENT`    | server → client | reliable event |
| 6          | `LEAVE`    | client → server | none |
| 7          | `EVENT_ACK`| client → server | acknowledged event ids |

Senders must use the direction listed. Unexpected directions are ignored by
the receiver.

### 1. JOIN (client → server)

| Offset | Size | Field               | Type  |
|-------:|-----:|---------------------|-------|
| 22     | 1    | `desired_player_id` | uint8 |
| 23     | 1    | `capabilities`      | uint8 |

Struct: `<BB`. Requests admission, optionally claiming a specific player slot.
`desired_player_id = 0` means "any free slot". If the requested slot is
already occupied the server replies with a `JOIN_ACK` whose `player_id` is
`0` (denied); otherwise it assigns that slot. A client that receives no reply
may resend `JOIN` with a new `seq`.

#### Capability bits (`capabilities`)

| Bit | Mask | Name              | Meaning |
|----:|-----:|-------------------|---------|
| 0   | 0x01 | `DELTA_SNAPSHOTS` | Client can reconstruct delta snapshots |
| 1-7 |      | reserved          | Must be `0` |

A client that does not set `DELTA_SNAPSHOTS` always receives full snapshots.
Only C3 sets it.

### 2. JOIN_ACK (server → client)

| Offset | Size | Field       | Type  |
|-------:|-----:|-------------|-------|
| 22     | 1    | `player_id` | uint8 |

Struct: `<B`. Total packet size: 23 bytes.

`player_id` is the assigned entity id, in `1..255`. The value `0` means the
join was **denied** and the client must stop retrying (for example, the
requested slot is already taken).

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
| 10          | 1    | `buttons`   | uint8 | Button bit flags (see below) |

The server ignores any record whose `input_seq` is not greater than the last
accepted `input_seq` for that player, so repeated (redundant) records are
safe. `count` may be `0`.

#### Button bits (`buttons`)

| Bit | Mask | Name      | Meaning |
|----:|-----:|-----------|---------|
| 0   | 0x01 | `ATTACK`  | Attack while held; server fires on the rising edge, subject to cooldown |
| 1   | 0x02 | `REMATCH` | Request a restart after a KO (server only acts when the round is over) |
| 2-7 |      | reserved  | Must be `0` |

Attack resolution is entirely server-side: the server checks cooldown, range
and target, applies damage, and then reports the result in the next
`SNAPSHOT`.

### 4. SNAPSHOT (server → client)

| Offset | Size | Field              | Type   | Meaning |
|-------:|-----:|--------------------|--------|---------|
| 22     | 4    | `snapshot_id`      | uint32 | Server snapshot counter, starts at 1 |
| 26     | 4    | `base_snapshot_id` | uint32 | `0` = full snapshot, else delta base |
| 30     | 1    | `entity_count`     | uint8  | Number of entity records (max 255) |
| 31     | 1    | `removed_count`    | uint8  | Number of removed entity ids (max 255) |
| 32     | ...  | `entities[entity_count]` | | Each entity record is 11 bytes |
| ...    | ...  | `removed_ids[removed_count]` | uint8 each | Entities to delete |

Each entity record (11 bytes, format `<BhhhhBB`):

| Rel. offset | Size | Field       | Type  | Meaning |
|------------:|-----:|-------------|-------|---------|
| 0           | 1    | `entity_id` | uint8 | Player/entity id |
| 1           | 2    | `x`         | int16 | World x position (rounded) |
| 3           | 2    | `y`         | int16 | World y position (rounded) |
| 5           | 2    | `vx`        | int16 | x velocity (units/second, rounded) |
| 7           | 2    | `vy`        | int16 | y velocity (units/second, rounded) |
| 9           | 1    | `health`    | uint8 | Authoritative health, `0..100` |
| 10          | 1    | `flags`     | uint8 | State bit flags (see below) |

#### Entity flags (`flags`)

| Bit | Mask | Name        | Meaning |
|----:|-----:|-------------|---------|
| 0   | 0x01 | `ATTACKING` | Attack animation window is active |
| 1   | 0x02 | `DEAD`      | Health has reached 0; entity cannot move or attack |
| 2-7 |      | reserved    | Always `0` |

#### Full vs delta snapshots

- `base_snapshot_id == 0` is a **full** snapshot: `entities` is the complete
  state and `removed_count` is `0`.
- `base_snapshot_id != 0` is a **delta** relative to the snapshot with that
  id. The client must already hold the base. Reconstruct by starting from the
  base's entities, replacing/adding every record in `entities`, then deleting
  every id in `removed_ids`.
- The server chooses the base per session as the newest snapshot the client
  has acknowledged (via the header `ack`), so deltas never depend on a
  snapshot the client may not have.
- The server also sends a full snapshot every `delta_keyframe_interval`
  snapshots (default 20) to bound recovery time and allow clients to resync.
- A delta whose base is missing on the client is ignored; the next keyframe
  resyncs it.

### 5. EVENT (server → client)

| Offset | Size | Field        | Type   | Meaning |
|-------:|-----:|--------------|--------|---------|
| 22     | 4    | `event_id`   | uint32 | Reliable-channel id |
| 26     | 1    | `event_type` | uint8  | Application-defined |
| 27     | ...  | `data`       | bytes  | Opaque, length = remainder |

Discrete events that cannot be replaced by a newer snapshot are delivered on
a small reliable channel. The server numbers events **per session** starting
at `1`, sends each `EVENT`, and retransmits it every `event_retry_interval`
until the client acknowledges it or `event_max_retries` is reached. The client
acknowledges via `EVENT_ACK`.

`event_type` values:

| Value | Name            | `data` |
|------:|-----------------|--------|
| 1     | `PLAYER_JOINED` | 1 byte player id |
| 2     | `PLAYER_LEFT`   | 1 byte player id |
| 3     | `KO`            | 1 byte player id of the player who reached 0 health |
| 4     | `REMATCH`       | empty |

### 6. EVENT_ACK (client → server)

| Offset | Size | Field       | Type   | Meaning |
|-------:|-----:|-------------|--------|---------|
| 22     | 4    | `event_ack` | uint32 | Highest event id received |
| 26     | 4    | `event_bits`| uint32 | Bitmap of earlier event ids |

Struct: `<II`. Uses the same semantics as the packet header: bit `i` (0 = LSB)
acknowledges event id `event_ack - 1 - i`.

### 7. LEAVE (client → server)

Header only. Announces an intentional disconnect. Best-effort; the server
also removes a client on its own timeout policy (not implemented in v5).

## Worked example

`JOIN_ACK` assigning player 1, with `seq = 0`, `ack = 1`, `ack_bits = 0`,
`tick = 0`:

```
31 30 43 4E   protocol_id = "NC01"
05            version
02            msg_type = JOIN_ACK
00 00 00 00   seq = 0
01 00 00 00   ack = 1
00 00 00 00   ack_bits = 0
00 00 00 00   tick = 0
01            player_id = 1
```

## Known v5 simplifications

- The server's `seq` is a single global counter shared across all clients.
  As a result a client can observe gaps caused by packets sent to other
  clients. Acknowledgement logic remains correct, but per-client gap counting
  is not meaningful. This will be made per-session before measuring loss.
- No packet-level checksum beyond the UDP/IP checksums.
- C0-C2 send one input record per packet; a lost packet can lose an attack
  press. C3 sends the last N inputs per packet to recover from this.
- Delta compression is entity-granular: a moving entity is sent whole, so
  bandwidth savings come from idle/unchanged entities, not from a moving
  player.
- A rematch is triggered by either player pressing `REMATCH` once the round
  is over; there is no confirmation handshake.
- There is no rejoin-with-same-id handshake beyond `JOIN`; a client that
  disconnects without `LEAVE` holds its slot until the server adds a timeout.
- Reliable events are retried at a fixed interval up to a bounded number of
  attempts; events older than the 32-entry ack window are treated as received
  if the client's ack has moved past them.
