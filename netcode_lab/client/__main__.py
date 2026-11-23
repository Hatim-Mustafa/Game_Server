from __future__ import annotations

import argparse
import asyncio
import logging

from netcode_lab.client.bot import run_headless
from netcode_lab.client.client import ClientConfig, GameClient
from netcode_lab.client.predicted_client import PredictedClient
from netcode_lab.config import ServerConfig


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="netcode-lab-client")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument(
        "--mode",
        choices=("c0", "c1"),
        default="c0",
        help="c0 = naive (draw server state), c1 = client-side prediction",
    )
    parser.add_argument(
        "--tick-rate",
        type=float,
        default=30.0,
        help="server tick rate; c1 must match the server's --tick-rate",
    )
    parser.add_argument(
        "--player",
        type=int,
        default=0,
        help="claim a specific player id (0 = any free id)",
    )
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument(
        "--input-rate",
        type=float,
        default=None,
        help="inputs per second (default: same as --tick-rate)",
    )
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
        input_rate=args.input_rate if args.input_rate is not None else args.tick_rate,
        player_slot=args.player,
    )
    if args.mode == "c1":
        client = PredictedClient(config, ServerConfig(tick_rate=args.tick_rate))
    else:
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

    predictor = getattr(client, "predictor", None)
    if predictor is not None:
        stats = predictor.stats
        print(
            f"C1 summary: {stats.corrections} corrections in {stats.reconciliations} "
            f"snapshots ({stats.rate:.1%}), mean {stats.mean_px:.1f}px, "
            f"max {stats.max_px:.1f}px"
        )


if __name__ == "__main__":
    main()
