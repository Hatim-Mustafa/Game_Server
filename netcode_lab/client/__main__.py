from __future__ import annotations

import argparse
import asyncio
import logging

from netcode_lab.client.bot import run_headless
from netcode_lab.client.client import ClientConfig, GameClient


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="netcode-lab-client")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument("--input-rate", type=float, default=30.0)
    parser.add_argument("--print-interval", type=float, default=0.5)
    parser.add_argument("--csv", default=None)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = ClientConfig(
        host=args.host,
        port=args.port,
        input_rate=args.input_rate,
    )
    client = GameClient(config)
    try:
        asyncio.run(
            run_headless(
                client,
                duration=args.duration,
                print_interval=args.print_interval,
                csv_path=args.csv,
            )
        )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
