# AGENTS.md

Guidance for AI agents and contributors working in this repository.

## What this project is

NetCode Lab: a two-player, top-down arena game with an **authoritative
server** communicating over a **custom binary UDP protocol**. It exists to
compare netcode techniques empirically:

- `C0` naive client (render server state)
- `C1` client-side prediction + reconciliation (implemented)
- `C2` entity interpolation of remote players (implemented)
- `C3` input redundancy + delta compression (implemented)
- TCP baseline (not yet implemented)

The course project spec is `docs/CN_Project_Proposal.pdf`. Concept notes are
in `docs/CONCEPTS.md`; the **frozen wire format** is `docs/PROTOCOL.md`.

## Environment

- Python **3.11+**, standard library plus `pygame` (game window only).
- No packaging/install step: run everything **from the repository root** so
  that `netcode_lab` is importable.
- Tests use the stdlib `unittest` runner and do **not** require pygame.
- Linux is required only for the planned `netns`/`tc netem` topology. The
  game, clients and tests run on Windows and Linux. Do not rely on WSL2 for
  `netem` (unreliable).

## Commands

| Task | Command |
|------|---------|
| Install UI dependency | `pip install -r requirements.txt` |
| Run server | `python -m netcode_lab.server --port 9000` |
| Run headless client | `python -m netcode_lab.client --mode c0 --port 9000` |
| Run C1 headless client | `python -m netcode_lab.client --mode c1 --tick-rate 30` |
| Run C2 headless client | `python -m netcode_lab.client --mode c2 --tick-rate 30 --interpolation-delay-ms 100` |
| Run C3 headless client | `python -m netcode_lab.client --mode c3 --tick-rate 30 --redundancy 3` |
| Run pygame, player 1 | `python pygame_fighter.py --player 1 --mode c0` |
| Run pygame, player 2 | `python pygame_fighter.py --player 2 --mode c0` |
| Run pygame in C2 | `python pygame_fighter.py --player 1 --mode c2 --tick-rate 30` |
| Run pygame in C3 | `python pygame_fighter.py --player 1 --mode c3 --tick-rate 30` |
| Run all tests | `python -m unittest discover -s tests -v` |
| Quick syntax check | `python -m py_compile <file>` |
| Build netns topology (Linux) | `sudo topology/setup.sh` |
| Verify topology | `sudo topology/verify.sh` |
| Apply impairment | `sudo topology/netem.sh apply --rtt-ms 100 --loss 2` |
| Clear impairment | `sudo topology/netem.sh clear` |
| Tear down topology | `sudo topology/teardown.sh` |
| Run experiments | `python -m experiments.run --conditions local:0:0 --modes c0,c1 --runs 1 --duration 5` |
| Run over netns with netem | `sudo python -m experiments.run --netns --apply-netem` |
| Aggregate results | `python -m experiments.aggregate` |

Server flags: `--host --port --tick-rate --snapshot-rate --log-level`.
Client flags: `--host --port --mode {c0,c1,c2,c3} --tick-rate
--interpolation-delay-ms --redundancy --player --duration --input-rate
--print-interval --csv --log-level`.
Game flags: `--player {1,2}` (required), `--mode {c0,c1,c2,c3}`, `--tick-rate`,
`--interpolation-delay-ms`, `--redundancy`, `--host`, `--port`.

There is **no configured linter or formatter**. Verify changes with the test
suite and `py_compile`.

## Layout

```
netcode_lab/
  config.py               ServerConfig (rates, world, combat constants)
  protocol/
    messages.py           wire format + PROTOCOL VERSION + flags/buttons
    ack.py                sequence/acknowledgement window
  server/
    world.py              authoritative simulation + combat logic
    server.py             asyncio UDP endpoint, sessions, tick/snapshot loops
    __main__.py           server CLI
  client/
    client.py             C0 client core (join, input, snapshot state, ack)
    predicted_client.py   C1 client (prediction + reconciliation wiring)
    prediction.py         pure Predictor logic and correction stats
    interpolated_client.py C2 client (C1 + delayed remote interpolation)
    interpolation.py      pure SnapshotInterpolator logic
    compressed_client.py  C3 client (C2 + redundancy + delta capability)
    metrics.py            client metrics (jitter/stalls/bytes/latency/error)
    bot.py                headless patrol bot + CSV + summary JSON
    __main__.py           client CLI (--mode c0/c1/c2/c3)
pygame_fighter.py         one window per player; renders server state
topology/                 netns + veth + routing + tc netem scripts
experiments/              experiment runner + aggregator
tests/                    unit + integration tests
docs/                     proposal, concepts, protocol
```

## Architecture essentials

- **Server is authoritative.** Clients send inputs; the server owns
  positions, health, hits, KO and rematch. Clients only ask and render.
- Two independent loops: `_tick_loop` (fixed `dt`, simulation) and
  `_snapshot_loop` (broadcast). Both deadline-accumulate to avoid drift.
- Sessions are keyed by source `(ip, port)` in `sessions_by_addr` and by
  `player_id` in `sessions_by_player`.
- `JOIN` carries a desired `player_id` (`0` = any). The server claims it if
  free, otherwise replies `JOIN_ACK` with `player_id = 0` (denied). Clients
  set `rejected` and must stop.
- Every packet header carries `seq`, `ack`, `ack_bits` (32-packet bitmap) and
  `tick`. Prediction, redundancy and deltas all depend on ack correctness.
- C1 derives "which input the server has applied" from the snapshot header
  `ack`, because snapshots carry no explicit processed-input field.
- C2 = C1 + interpolation. The local player stays predicted; remote players
  are sampled from a tick-ordered snapshot buffer `interpolation_delay` behind
  the estimated server timeline. C2 is client-only (no wire/server change).
- `GameClient.latest_snapshot` is **monotonic**: only newer `snapshot_id`s
  replace it (out-of-order UDP packets must not roll state back). The actual
  last-received snapshot is kept in `last_received_snapshot` for the
  interpolator, which needs the packet's own tick.
- C3 = C2 + input redundancy + delta snapshots. The client repeats the last N
  inputs in each INPUT packet (server dedupes by `input_seq`) and advertises
  `Capability.DELTA_SNAPSHOTS` in JOIN. The server then sends a delta against
  the newest snapshot the client acknowledged (mapped from the header `ack`),
  plus a full keyframe every `ServerConfig.delta_keyframe_interval`
  snapshots. `GameClient` reconstructs deltas into full snapshots before
  prediction/interpolation see them.
- Discrete events (join/leave/KO/rematch) use a per-session reliable channel:
  the server numbers `EVENT`s, retransmits until `EVENT_ACK` covers them, and
  gives up after `event_max_retries`. Do not move supersedable state onto this
  channel; snapshots own state.

## Metrics and experiments

- `netcode_lab/client/metrics.py` collects snapshot rate/jitter/stalls/gaps,
  bytes sent/received, input-to-visible latency, and rendered-vs-authoritative
  position error. It is fed by `GameClient` and the headless bot.
- The bot writes `*.csv` plus a sibling `*.summary.json` per client run. The
  aggregator reads the summary JSONs, groups by (condition, mode), and writes
  `summary.csv`, `summary.md`, and optional `plots/`.
- `experiments/run.py` drives conditions × modes × runs, can run inside the
  topology namespaces (`--netns`) and apply `netem` (`--apply-netem`, needs
  passwordless `sudo -n`), and records `meta.json` per run.
- Results live under `results/`, which is gitignored; do not commit them.
- C0 position error is 0 by construction; C1+ includes prediction lead. Use
  correction metrics for misprediction and latency for responsiveness.

## Non-negotiable rules

1. **Wire format changes require a version bump.** Update `VERSION` in
   `netcode_lab/protocol/messages.py`, update `docs/PROTOCOL.md`, and update
   affected tests in the same change. Currently `VERSION = 5`.
2. **Keep client and server movement identical.** `Predictor.step`
   (`prediction.py`) must mirror `World.tick` (`world.py`): same speed, same
   diagonal normalisation, same clamping. Otherwise every snapshot causes a
   correction. `tests/test_prediction.py` enforces this.
3. **C1 requires input rate == server tick rate.** The predictor applies one
   tick per input. Pass matching `--tick-rate` to server and C1 clients.
4. **Packets are little-endian** with the struct formats documented in
   `docs/PROTOCOL.md`; do not hand-roll offsets elsewhere.
5. **The headless client CLI and the game must use the same client classes.**
   Do not reimplement networking or combat in `pygame_fighter.py`; it is a
   renderer plus input mapping only.
6. Do not commit secrets. There are none expected here.

## Conventions observed in the codebase

- `from __future__ import annotations` at the top of modules.
- `dataclasses` for state/config; type hints on public functions.
- `asyncio` for all networking; no threads.
- Logging via module-level `log = logging.getLogger(__name__)`.
- Tests are organized by concern: `test_protocol.py`, `test_world.py`,
  `test_combat.py`, `test_prediction.py`, `test_interpolation.py`,
  `test_c3.py`, `test_events.py`, `test_metrics.py`, `test_experiments.py`,
  `test_integration.py`.
- Keep gameplay constants in `ServerConfig` (and mirror them in the
  predictor), not scattered literals.

## Known gotchas

- The server's outgoing `seq` is a **global counter shared by all clients**,
  so per-client gap/loss counting from server seqs is meaningless. Fix by
  making it per-session before measuring loss.
- `test_prediction` includes a timing-sensitive C1 integration test that can
  occasionally flake on Windows (`rate <= 0.1`). Prefer running the suite on
  the Ubuntu VM for trustworthy numbers; do not "fix" by loosening the
  assert unless justified.
- Pygame `--player` slots are held until the client sends `LEAVE`; a hard
  kill holds the slot until a server timeout is added.
- `pygame` must be installed to run the game, but not to run the tests.
- Entity snapshot record is 11 bytes (`<BhhhhBB`) with `health` and `flags`;
  flags are `ATTACKING` and `DEAD`. Buttons are `ATTACK` and `REMATCH`.
- C2's effective opponent delay is roughly `interpolation_delay` + one-way
  network delay, because the playback clock is anchored to the newest
  snapshot's tick on arrival. Health/flags are taken from the newest snapshot,
  not the delayed timeline, so a remote can show damage slightly before the
  delayed punch pose reaches the target.
- C3 delta compression is entity-granular, so a moving player is still sent
  whole; savings come from idle/unchanged entities. C3 redundancy recovers up
  to N-1 consecutive lost INPUT packets (one input interval of extra latency
  if the newest packet is lost).
- `topology/` requires Linux root and a real kernel with `sch_netem`; it will
  not work on Windows or reliably under WSL2. The game and tests still run on
  Windows.
- Reliable events are best-effort after `event_max_retries` (default 20 at
  0.25 s); they are not a general-purpose guaranteed channel.
- `experiments.run` without `--apply-netem` still labels conditions (e.g.
  `rtt100`) but the impairment is not applied; `meta.json` records
  `netem_applied`, so never present un-impaired runs as impaired.
- `--netns --apply-netem` requires root and passwordless `sudo -n`; run
  `topology/setup.sh` first. `sys.executable` runs inside the namespaces.

## When adding a feature

1. Read `docs/PROTOCOL.md` and `docs/CONCEPTS.md` first.
2. Decide whether the wire format changes; if so, bump `VERSION` and update
   the doc.
3. Put pure logic in a testable module (see `prediction.py`), keep networking
   in the client/server classes.
4. Add tests; run the full suite.
5. Update `README.md` for user-facing flags or run instructions.
