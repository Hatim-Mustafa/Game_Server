# Game_Server

A study on the advancements made in game servers.

## NetCode Lab

Authoritative two-player arena game over a custom binary UDP protocol. It
evaluates client-side prediction, entity interpolation and input redundancy
under controlled delay and packet loss, with a TCP baseline for comparison.
See `docs/CONCEPTS.md` for the concept reference and the input/snapshot
walkthrough, and `docs/PROTOCOL.md` for the frozen wire format.

## Layout

```
netcode_lab/
  config.py            server and simulation parameters
  protocol/
    messages.py        binary wire format (all message types)
    ack.py             sequence/acknowledgement window
  server/
    world.py           authoritative simulation state
    server.py          UDP endpoint, sessions, tick and snapshot loops
    __main__.py        command-line entry point
  client/
    client.py          C0 client core (join, input, snapshot state)
    predicted_client.py  C1 client (prediction + reconciliation)
    prediction.py        predictor logic and correction stats
    interpolated_client.py  C2 client (C1 + remote interpolation)
    interpolation.py     snapshot interpolator
    compressed_client.py   C3 client (C2 + redundancy + delta snapshots)
    bot.py             headless patrol bot and reporting
    __main__.py        command-line entry point
tests/                 protocol, ack window, world and integration tests
docs/                  concept and protocol notes
```

## Running the server

Requires Python 3.11+.

```
python -m netcode_lab.server --host 0.0.0.0 --port 9000
```

Options: `--tick-rate` (default 30), `--snapshot-rate` (default 20),
`--log-level` (default INFO).

## Running a headless C0 client

The C0 client sends inputs and displays the authoritative state from
snapshots only (no prediction or interpolation).

```
python -m netcode_lab.client --host 127.0.0.1 --port 9000 --duration 10
```

It patrols automatically and prints player position/velocity every
`--print-interval` seconds. Add `--csv out.csv` to record a per-sample trace
for later analysis. Run two clients against one server to see both entities.

## Running a C1 (prediction) client

C1 applies each input locally straight away and reconciles with the server
when snapshots arrive (see `netcode_lab/client/prediction.py`).

```
python -m netcode_lab.client --mode c1 --host 127.0.0.1 --port 9000 --duration 10
```

`--tick-rate` must match the server's `--tick-rate` (default 30 for both).
Each printed line adds `pending` (inputs not yet confirmed) and
`corrections=N/M` (snapshots that moved the player by more than 1 unit, out of
snapshots used). A summary with correction rate, mean and max size is printed
at the end. With `--csv`, the columns `mode`, `pending_inputs`,
`reconciliations`, `corrections` and `last_correction_px` are added (zeros for
C0).

## Running a C2 (interpolation) client

C2 is C1 plus a delayed interpolation buffer for **remote** players
(`netcode_lab/client/interpolation.py`). The local player stays predicted; the
opponent is rendered `interpolation_delay` behind the estimated server
timeline so movement is smooth under jitter/loss.

```
python -m netcode_lab.client --mode c2 --host 127.0.0.1 --port 9000 \
    --tick-rate 30 --interpolation-delay-ms 100 --duration 10
```

`--tick-rate` must match the server. `--interpolation-delay-ms` (default 100)
sets the playback delay; the effective visible delay is roughly this plus the
one-way network delay. Correction metrics are reported the same way as C1.

## Running a C3 (redundancy + delta) client

C3 is C2 plus two robustness/bandwidth features:

- **Input redundancy** — each INPUT packet repeats the last `--redundancy`
  inputs (default 3), so a lost packet doesn't lose an attack press or a
  direction change.
- **Delta-compressed snapshots** — C3 advertises the `DELTA_SNAPSHOTS`
  capability in JOIN; the server then sends each snapshot as a delta against
  the newest snapshot the client acknowledged, with a full keyframe every
  `delta_keyframe_interval` (default 20) snapshots.

```
python -m netcode_lab.client --mode c3 --host 127.0.0.1 --port 9000 \
    --tick-rate 30 --interpolation-delay-ms 100 --redundancy 3 --duration 10
```

`--tick-rate` must match the server. `--redundancy` trades INPUT bandwidth for
loss tolerance. C0-C2 clients do not advertise the capability and keep
receiving full snapshots.

## Running the pygame fighter (server-authoritative)

Run **one window per player**. Each window is a separate client that claims a
specific player slot from the server, so you can put the two windows side by
side. All movement, attacks, health, KO and rematch are decided by the
server. Install the UI dependency first:

```
pip install -r requirements.txt
python -m netcode_lab.server --port 9000

# terminal 2: player 1
python pygame_fighter.py --player 1 --mode c0

# terminal 3: player 2
python pygame_fighter.py --player 2 --mode c0
```

If a player slot is already claimed (for example you launch a second
`--player 1`), the new window prints `Player slot 1 is already in use.` and
closes.

Pick the netcode phase independently per window with `--mode`:

- `--mode c0` — draw the server's authoritative state (default)
- `--mode c1` — client-side prediction for the local fighter
- `--mode c2` — C1 plus interpolated rendering of the opponent
- `--mode c3` — C2 plus input redundancy and delta snapshots

Each window renders its own player via its own client (predicted in
`c1`/`c2`/`c3`) and the opponent from snapshots (`c2`/`c3` interpolate it).
Running different modes per window lets you compare them side by side. Set
the same `--tick-rate` on server and any `c1`/`c2`/`c3` client:

```
python pygame_fighter.py --player 1 --mode c3 --tick-rate 30 \
    --interpolation-delay-ms 100 --redundancy 3
```

Flags: `--player {1,2}` (required), `--mode {c0,c1,c2,c3}`, `--tick-rate`,
`--interpolation-delay-ms` (C2/C3), `--redundancy` (C3), `--host`, `--port`.
The active mode and player are shown in the window.

Controls: player 1 uses `WASD` + `F` (attack); player 2 uses arrow keys + `K`
(attack); `R` = rematch after a KO; `ESC` = quit.

The server applies attack damage only on the rising edge of the attack
button, when the target is within `attack_range` and the cooldown has
expired. Health (`0..100`), the attacking flag and the dead flag are carried
in each snapshot; see `docs/PROTOCOL.md`.

## Tests

```
python -m unittest discover -s tests -v
```
