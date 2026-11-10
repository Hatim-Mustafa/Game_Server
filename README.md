# Game_Server

A study on the advancements made in game servers.

## NetCode Lab

Authoritative two-player arena game over a custom binary UDP protocol. It
evaluates client-side prediction, entity interpolation and input redundancy
under controlled delay and packet loss, with a TCP baseline for comparison.
See `docs/CONCEPTS.md` for the concept reference and the input/snapshot
walkthrough.

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
tests/                 unit tests for protocol, ack window and world
docs/                  concept and design notes
```

## Running the server

Requires Python 3.11+.

```
python -m netcode_lab.server --host 0.0.0.0 --port 9000
```

Options: `--tick-rate` (default 30), `--snapshot-rate` (default 20),
`--log-level` (default INFO).

## Tests

```
python -m unittest discover -s tests -v
```
