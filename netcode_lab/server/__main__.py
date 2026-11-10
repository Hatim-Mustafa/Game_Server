from __future__ import annotations

import argparse
import asyncio
import logging

from netcode_lab.config import ServerConfig
from netcode_lab.server.server import GameServer


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="netcode-lab-server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--tick-rate", type=float, default=30.0)
    parser.add_argument("--snapshot-rate", type=float, default=20.0)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = ServerConfig(
        host=args.host,
        port=args.port,
        tick_rate=args.tick_rate,
        snapshot_rate=args.snapshot_rate,
    )
    try:
        asyncio.run(GameServer(config).run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
