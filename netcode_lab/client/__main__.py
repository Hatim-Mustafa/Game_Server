from __future__ import annotations

import argparse
import asyncio
import logging

from netcode_lab.client.bot import run_headless
from netcode_lab.client.client import ClientConfig, GameClient
from netcode_lab.client.compressed_client import CompressedClient
from netcode_lab.client.interpolated_client import InterpolatedClient
from netcode_lab.client.predicted_client import PredictedClient
from netcode_lab.config import ServerConfig


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="netcode-lab-client")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument(
        "--mode",
        choices=("c0", "c1", "c2", "c3"),
        default="c0",
        help="c0 naive, c1 prediction, c2 +interpolation, c3 +redundancy/delta",
    )
    parser.add_argument(
        "--tick-rate",
        type=float,
        default=30.0,
        help="server tick rate; c1/c2/c3 must match the server's --tick-rate",
    )
    parser.add_argument(
        "--interpolation-delay-ms",
        type=float,
        default=100.0,
        help="c2/c3: how far behind the server timeline to render remote players",
    )
    parser.add_argument(
        "--redundancy",
        type=int,
        default=3,
        help="c3: how many recent inputs to repeat in each INPUT packet",
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
    if args.interpolation_delay_ms < 0:
        parser.error("--interpolation-delay-ms must be non-negative")
    if args.redundancy < 1:
        parser.error("--redundancy must be at least 1")

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = ClientConfig(
        host=args.host,
        port=args.port,
        input_rate=args.input_rate if args.input_rate is not None else args.tick_rate,
        player_slot=args.player,
        redundancy=args.redundancy,
    )
    if args.mode == "c3":
        client = CompressedClient(
            config,
            ServerConfig(tick_rate=args.tick_rate),
            interpolation_delay=args.interpolation_delay_ms / 1000.0,
        )
    elif args.mode == "c2":
        client = InterpolatedClient(
            config,
            ServerConfig(tick_rate=args.tick_rate),
            interpolation_delay=args.interpolation_delay_ms / 1000.0,
        )
    elif args.mode == "c1":
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
            f"{args.mode.upper()} summary: {stats.corrections} corrections in "
            f"{stats.reconciliations} snapshots ({stats.rate:.1%}), "
            f"mean {stats.mean_px:.1f}px, max {stats.max_px:.1f}px"
        )


if __name__ == "__main__":
    main()
