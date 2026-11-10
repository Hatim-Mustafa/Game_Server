# NetCode Lab — Concepts and Feature Reference

This document explains, from first principles, every feature listed in
`CN_Project_Proposal.pdf`. It is meant as a shared reference so all three
group members use the same vocabulary and the same mental model before any
code is written. It contains no implementation code.

---

## Part A — Base concepts

Everything in this project is a response to one root problem:

> The network is slow and lossy, but the game must feel instant.

### Game server

In a multiplayer game someone has to be the source of truth. If each client
trusted itself, two players could both believe they hit each other. So one
machine — the **server** — owns the real game state (positions, health,
scores). Clients only send "what I want to do" and receive "here is what
actually happened".

### Authoritative server

The server's state is final. Clients are not allowed to change it directly.
They only:

- *request* changes by sending **inputs**, and
- *display* results from **snapshots**.

This prevents cheating and desynchronisation.

### Tick

The server updates the game in fixed steps, for example 30 times per second.
Each step is a **tick**. Game logic is measured in ticks rather than
wall-clock time, which makes behaviour deterministic and reproducible for the
experiments.

### Snapshot

Periodically the server packages the current state (player positions,
velocities, scores, etc.) and sends it to clients. That package is a
**snapshot**. Clients draw the world from snapshots.

### Latency, RTT, jitter, loss

- **RTT** — round-trip time. 50 ms RTT means roughly 25 ms each direction.
- **Jitter** — variation in delay, so packets arrive unevenly.
- **Loss** — some packets never arrive.

The whole project asks: what happens to the game's feel as these worsen, and
which techniques mitigate it?

### Why UDP instead of TCP

- **TCP** guarantees that every byte arrives, in order. Great for files. But
  if one packet is lost, everything behind it waits for a retransmission —
  this is **head-of-line blocking**. In a fast game, a stale packet is
  useless; you want the newest state, not the delayed old one.
- **UDP** sends datagrams with no guarantees: they may be lost, duplicated or
  reordered. That sounds worse, but it means the application decides what to
  do when something is lost — which is exactly what game netcode is about.

The game therefore runs over **UDP with a small custom reliability layer**,
and a **TCP version** exists purely to demonstrate the head-of-line-blocking
downside in the results.

---

## Part B — The features

### 1. Authoritative multiplayer game

Two players in a top-down arena. The server runs the simulation; clients send
inputs and receive snapshots. This is the foundation all other features attach
to: a loop that receives inputs, updates state, and broadcasts snapshots.

### 2. Custom binary UDP protocol

UDP gives raw bytes with no structure, so the group defines its own message
format. Every message begins with a **protocol ID** (to discard unrelated
traffic), then a **message type**, then fields.

Message types:

- `JOIN` / `JOIN_ACK` — a client asks to enter; the server accepts and
  assigns a player ID.
- `INPUT` — a client sends movement or intent.
- `SNAPSHOT` — the server sends world state.
- `EVENT` — discrete occurrences such as a score or a departure.
- `LEAVE` — a clean disconnect.

Every packet also carries **sequence numbers** and **acknowledgements** so
each side knows which packets the other has received. Prediction, redundancy
and delta compression all depend on this shared knowledge of "what has the
other side already seen?". The **ack bitfield** is a compact way to
acknowledge several recent packets in one field.

### 3. Client-side prediction (C1)

**Problem:** if the client waits for the server, every key press takes a full
round trip to appear on screen. At 50 ms RTT that is visibly laggy; at 200 ms
it is unplayable.

**Fix:** when the player presses a direction, the client immediately moves
the character locally *and* sends the input to the server. Response feels
instant.

**But** the server may disagree (collisions, another player, rules). So when a
snapshot arrives the client **reconciles**: it adopts the authoritative state,
then **replays** every input the server has not yet acknowledged. If the
replayed result differs from the local prediction, the character visibly
"snaps" to the corrected position. Fewer and smaller corrections mean better
netcode — this is why *correction rate* and *correction size* are metrics.

### 4. Entity interpolation (C2)

**Problem:** snapshots arrive only about 20 times per second and irregularly,
so remote players would teleport in small jumps.

**Fix:** the client renders *other* players slightly in the past (for example
100 ms behind) and smoothly slides them between the last two known positions.
Accuracy is traded for smoothness. The local player is *not* interpolated,
because it is rendered from prediction.

### 5. Input redundancy (C3)

**Problem:** with UDP, a lost `INPUT` packet means the server never applies
that movement, which causes a correction.

**Fix:** each `INPUT` packet carries the most recent **N** inputs, not only the
newest. If packet 10 is lost but packet 11 arrives, packet 11 still contains
input 10, so the server can fill the gap. Costs a little bandwidth, saves
corrections under loss. This tradeoff is one of the hypotheses to test.

### 6. Delta-compressed snapshots (C3)

**Problem:** sending full state in every snapshot wastes bandwidth,
especially once redundancy is added.

**Fix:** send only what **changed** since the last snapshot the client
acknowledged. The client reconstructs the full state by applying the delta.
This relies on the acknowledgement mechanism from feature 2, and must fall
back to a full snapshot if the baseline snapshot is missing.

### 7. Reliable event delivery

Position snapshots are safe to lose because a newer one supersedes them. But
"you scored" or "player joined" cannot be replaced by the next state, so
those messages use a small **acknowledged (reliable) channel**: send, wait for
ack, resend if needed. This re-implements, by hand, the reliability TCP gives
for free — but only for the few messages that need it.

### 8. TCP baseline

The same game over TCP instead of UDP, to show **head-of-line blocking**: under
packet loss TCP stalls waiting for retransmissions while the UDP stack keeps
flowing. The results table compares them.

### 9. Configurations C0–C3

The same game at four cumulative levels, so each technique's effect can be
isolated:

| Config | Includes | Purpose |
|--------|----------|---------|
| **C0** naive | draw server snapshots only | laggy baseline |
| **C1** | + prediction and reconciliation | isolate prediction's effect |
| **C2** | + interpolation of remote players | isolate interpolation's effect |
| **C3** | + input redundancy and delta compression | full stack |

Running C0 to C3 under identical conditions answers: *how much does each
technique help, and at what bandwidth cost?*

---

## Part C — Socket programming you actually need

Only a small, well-understood subset is required:

- **UDP socket** — bind to an address/port, `sendto`/`recvfrom` with a byte
  buffer. No connection, no ordering. Messages are packed and unpacked by
  hand.
- **TCP socket** — `connect`/`accept` and `send`/`recv` over a **stream**. A
  stream has no message boundaries, so a length prefix must be added to frame
  each message.
- **Non-blocking / async** — Python `asyncio` lets the server handle several
  clients and the game loop together; this is why the proposal references
  `asyncio`.
- **Timing** — the server tick loop and the client render loop run at
  different rates and must be buffered against each other.

---

## Part D — Walkthrough: one input -> snapshot round trip

This traces a single movement from key press to corrected render, showing how
sequence numbers, acks, prediction, redundancy and deltas fit together. The
example uses:

- Server tick rate: 30 Hz (a tick every ~33 ms)
- Snapshot rate: 20 Hz (a snapshot every ~50 ms)
- Redundancy N = 3 (each input packet repeats the last 3 inputs)
- Client A RTT: 50 ms (25 ms each way)
- Interpolation delay for remote players: 100 ms

Notation: `I<n>` is input number n, `S<n>` is the snapshot sent after tick n.
Times below are illustrative.

### Step-by-step

```
t=0ms    Client A: player presses RIGHT.
         Client A applies I41 locally immediately (prediction) -> character moves now.

t=1ms    Client A sends INPUT packet:
             seq=101, ack=<last snapshot acked by client>,
             inputs=[I39, I40, I41]        <- redundancy (last 3)

         Server receives it at t=26ms.

t=26ms   Server applies I39, I40, I41 (dedupes any it already had),
         advances simulation.

t=33ms   Server tick fires. Snapshot S12 is produced from authoritative state.
         Server notes "client ack covers up to my snapshot 11", so S12 is
         delta-compressed against S11 and only changed fields are sent.

t=34ms   Server sends SNAPSHOT S12:
             seq=210, ack=101 (and bitfield acknowledging recent packets),
             delta_based_on=11, changed fields only.

t=59ms   Client A receives S12 (25 ms later).
         Client A:
           1. Reconstructs full state from last acked snapshot (S11) + delta.
           2. Sees the server has processed inputs up to I41 (ack=101).
           3. Adopts authoritative position for the local player.
           4. Has no unacknowledged inputs left, so nothing to replay.
           5. If the adopted position differs from what prediction showed,
              it snaps (a correction was recorded).

         Client A sends its next INPUT with ack=<S12 acknowledged>.

routed to Client B (through both routers + netem delay):
t=59ms    Client B receives S12 (remote player's state).
         Client B does NOT apply it immediately. It is rendering 100 ms in the
         past, so it buffers S12 and interpolates between S11 and S12 as its
         render clock passes through them -> smooth motion, no teleporting.
```

### What happens when a packet is lost

Suppose `INPUT` seq=101 (carrying I39,I40,I41) is dropped by `netem`:

- Client A still feels instant movement (prediction already applied I41).
- Client A sends seq=102 with `inputs=[I40, I41, I42]`.
- The server receives seq=102 and recovers I40, I41 from the redundancy. Only
  I39 might be genuinely missing, and it was likely already delivered in an
  earlier packet.
- Because the server still learned about I41, the eventual snapshot matches
  the client's prediction, so **no correction occurs**. That is input
  redundancy's whole purpose.

### What happens with prediction disabled (C0)

- At t=0 the key press does nothing locally.
- The character only moves at t=59ms when the snapshot arrives, roughly one
  RTT later. This is the naive baseline: simple, but the input latency grows
  directly with RTT.

### What happens with interpolation disabled (C2 removed)

- Client B applies each snapshot the moment it arrives, so the remote player
  jumps ~50 ms of movement at a time instead of sliding. This is the visual
  "jitter" the interpolation metric measures.

### The key insight to remember

Every advanced feature is bookkeeping about **time and acknowledgement**:

- prediction needs to know which inputs are still unacknowledged (to replay);
- redundancy needs input history numbers (to resend old inputs);
- delta compression needs snapshot acknowledgement (to pick a baseline);
- interpolation needs a history of snapshots (to slide between them).

Get the sequence/ack model right first, and the rest follows naturally.
