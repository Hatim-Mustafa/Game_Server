from __future__ import annotations

import asyncio
import csv
import time
from typing import TextIO

from netcode_lab.client.client import GameClient

PATROL_PHASES = ((1, 0), (0, 1), (-1, 0), (0, -1))
CSV_COLUMNS = (
    "t",
    "joined",
    "player_id",
    "snapshot_id",
    "x",
    "y",
    "vx",
    "vy",
    "server_tick",
    "snapshots_received",
)


def patrol_direction(elapsed: float, period: float = 1.5) -> tuple[int, int]:
    return PATROL_PHASES[int(elapsed / period) % len(PATROL_PHASES)]


async def run_headless(
    client: GameClient,
    duration: float,
    print_interval: float = 0.5,
    csv_path: str | None = None,
    tick_interval: float = 0.05,
) -> None:
    csv_file: TextIO | None = None
    writer: csv.writer | None = None
    if csv_path:
        csv_file = open(csv_path, "w", newline="", encoding="utf-8")
        writer = csv.writer(csv_file)
        writer.writerow(CSV_COLUMNS)

    runner = asyncio.create_task(client.run(duration=duration))
    start = time.monotonic()
    last_print = -1.0
    try:
        while not runner.done():
            elapsed = time.monotonic() - start
            move_x, move_y = patrol_direction(elapsed)
            client.set_input(move_x, move_y)
            if elapsed - last_print >= print_interval:
                last_print = elapsed
                _report(client, elapsed, writer)
            await asyncio.sleep(tick_interval)
        await runner
    finally:
        if csv_file is not None:
            csv_file.close()


def _report(client: GameClient, elapsed: float, writer: csv.writer | None) -> None:
    entity = client.get_entity()
    snapshot = client.latest_snapshot
    snapshot_id = snapshot.snapshot_id if snapshot is not None else 0
    if entity is None:
        print(f"t={elapsed:5.2f}s waiting (joined={client.joined.is_set()})")
        x = y = vx = vy = 0
    else:
        x, y, vx, vy = entity.x, entity.y, entity.vx, entity.vy
        print(
            f"t={elapsed:5.2f}s player={client.player_id} snap={snapshot_id} "
            f"pos=({x},{y}) vel=({vx},{vy}) snaps={client.snapshots_received}"
        )
    if writer is not None:
        writer.writerow(
            (
                f"{elapsed:.3f}",
                int(client.joined.is_set()),
                client.player_id or 0,
                snapshot_id,
                x,
                y,
                vx,
                vy,
                client.tick,
                client.snapshots_received,
            )
        )
