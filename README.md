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

## Tests

```
python -m unittest discover -s tests -v
```
