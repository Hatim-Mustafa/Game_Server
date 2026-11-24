from __future__ import annotations

import asyncio
import csv
import json
import math
import os
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
    "mode",
    "pending_inputs",
    "reconciliations",
    "corrections",
    "last_correction_px",
    "snapshot_interval_ms",
    "snapshot_jitter_ms",
    "stalls",
    "snapshot_gaps",
    "sent_bytes",
    "recv_bytes",
    "sent_bps",
    "recv_bps",
    "input_latency_ms",
    "input_latency_mean_ms",
    "input_latency_samples",
    "position_error_px",
    "position_error_mean_px",
)


def patrol_direction(elapsed: float, period: float = 1.5) -> tuple[int, int]:
    return PATROL_PHASES[int(elapsed / period) % len(PATROL_PHASES)]


async def run_headless(
    client: GameClient,
    duration: float,
    print_interval: float = 0.5,
    csv_path: str | None = None,
    tick_interval: float = 0.02,
    patrol_period: float = 1.5,
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
    last_command = patrol_direction(0.0, patrol_period)
    pending_command: tuple[int, int] | None = None
    pending_since = 0.0
    completed = False
    try:
        while not runner.done():
            elapsed = time.monotonic() - start
            command = patrol_direction(elapsed, patrol_period)
            client.set_input(*command)
            if command != last_command:
                last_command = command
                if client.get_entity() is not None:
                    pending_command = command
                    pending_since = time.monotonic()

            entity = client.get_entity()
            now = time.monotonic()
            if pending_command is not None and entity is not None:
                if _direction_matches(entity, pending_command):
                    client.metrics.record_input_latency(now - pending_since)
                    pending_command = None
            server_entity = _server_entity(client)
            if entity is not None and server_entity is not None:
                client.metrics.record_position_error(
                    math.hypot(entity.x - server_entity.x, entity.y - server_entity.y)
                )

            if elapsed - last_print >= print_interval:
                last_print = elapsed
                _report(client, elapsed, writer)
            await asyncio.sleep(tick_interval)
        await runner
        completed = True
    finally:
        if csv_file is not None:
            csv_file.close()

    if completed:
        client.metrics.finish()
        duration_measured = time.monotonic() - start
        summary = _build_summary(client, duration_measured)
        _write_summary(csv_path, summary)
        _print_summary(summary)


def _sign(value: float) -> int:
    return (value > 0) - (value < 0)


def _direction_matches(entity, command: tuple[int, int]) -> bool:
    return (_sign(entity.vx), _sign(entity.vy)) == command


def _server_entity(client: GameClient):
    snapshot = client.latest_snapshot
    if snapshot is None or client.player_id is None:
        return None
    return next(
        (entity for entity in snapshot.entities if entity.entity_id == client.player_id),
        None,
    )


def _prediction_fields(client: GameClient) -> tuple[str, int, int, int, float]:
    predictor = getattr(client, "predictor", None)
    mode = getattr(client, "mode", "c0")
    if predictor is None:
        return mode, 0, 0, 0, 0.0
    stats = predictor.stats
    return (
        mode,
        len(predictor.pending),
        stats.reconciliations,
        stats.corrections,
        stats.last_px,
    )


def _report(client: GameClient, elapsed: float, writer: csv.writer | None) -> None:
    entity = client.get_entity()
    mode, pending, reconciliations, corrections, last_px = _prediction_fields(client)
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
            + (
                f" pending={pending} corrections={corrections}/{reconciliations}"
                f" last={last_px:.1f}px"
                if mode != "c0"
                else ""
            )
        )
    if writer is not None:
        metrics = client.metrics
        duration = max(elapsed, 1e-9)
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
                mode,
                pending,
                reconciliations,
                corrections,
                f"{last_px:.3f}",
                f"{metrics.last_snapshot_interval_ms:.2f}",
                f"{metrics.snapshot_jitter_ms:.2f}",
                metrics.stalls,
                metrics.snapshot_gaps,
                metrics.sent_bytes,
                metrics.recv_bytes,
                f"{metrics.sent_bytes / duration:.1f}",
                f"{metrics.recv_bytes / duration:.1f}",
                f"{metrics.input_latency_last_ms:.2f}",
                f"{metrics.input_latency_mean_ms:.2f}",
                len(metrics.input_latencies),
                f"{metrics.position_error_last_px:.2f}",
                f"{metrics.position_error_mean_px:.2f}",
            )
        )


def _build_summary(client: GameClient, duration: float) -> dict:
    mode, pending, reconciliations, corrections, last_px = _prediction_fields(client)
    predictor = getattr(client, "predictor", None)
    summary: dict = {
        "mode": mode,
        "player_id": client.player_id or 0,
        "duration_s": round(duration, 3),
        "pending_inputs": pending,
        "reconciliations": reconciliations,
        "corrections": corrections,
        "correction_rate": (
            corrections / reconciliations if reconciliations else 0.0
        ),
        "correction_last_px": last_px,
        "correction_mean_px": predictor.stats.mean_px if predictor else 0.0,
        "correction_max_px": predictor.stats.max_px if predictor else 0.0,
        "deltas_received": getattr(client, "deltas_received", 0),
        "events_received": client.events_received,
        "redundancy": getattr(client, "redundancy", 1),
    }
    summary.update(client.metrics.summary(duration))
    return summary


def _write_summary(csv_path: str | None, summary: dict) -> None:
    if not csv_path:
        return
    path = os.path.splitext(csv_path)[0] + ".summary.json"
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)


def _print_summary(summary: dict) -> None:
    print(
        "metrics: "
        f"snapshot_rate={summary['snapshot_rate_hz']:.1f}Hz "
        f"jitter={summary['snapshot_jitter_ms']:.1f}ms "
        f"stalls={summary['stalls']} gaps={summary['snapshot_gaps']} "
        f"in_lat={summary['input_latency_mean_ms']:.1f}ms "
        f"(n={summary['input_latency_samples']}) "
        f"pos_err={summary['position_error_mean_px']:.1f}px "
        f"recv={summary['recv_bytes_per_s']:.0f}B/s "
        f"send={summary['sent_bytes_per_s']:.0f}B/s"
    )
