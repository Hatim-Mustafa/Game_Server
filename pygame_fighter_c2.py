from __future__ import annotations

import argparse
import asyncio

import pygame

from netcode_lab.client.client import ClientConfig
from netcode_lab.client.interpolated_client import InterpolatedClient
from netcode_lab.config import ServerConfig
from netcode_lab.protocol.messages import FLAG_ATTACKING, FLAG_DEAD, Button
from pygame_fighter import (
    GROUND_Y,
    HEIGHT,
    WIDTH,
    _draw_health_bars,
    draw_fighter,
    entity_color,
    get_direction,
    winner_id,
    world_to_screen,
)


async def run_game(
    host: str,
    port: int,
    player_slot: int,
    tick_rate: float,
    interpolation_delay: float,
) -> None:
    pygame.init()
    pygame.display.set_caption(f"NetCode Lab - Player {player_slot} (C2 interpolation)")
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("consolas", 22)
    small_font = pygame.font.SysFont("consolas", 16)

    client = InterpolatedClient(
        ClientConfig(
            host=host, port=port, input_rate=tick_rate, player_slot=player_slot
        ),
        ServerConfig(tick_rate=tick_rate),
        interpolation_delay=interpolation_delay,
    )
    client_task = asyncio.create_task(client.run())
    running = True

    try:
        while running:
            clock.tick(60)
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False

            if client.rejected:
                print(f"Player slot {player_slot} is already in use. Closing.")
                break

            keys = pygame.key.get_pressed()
            if keys[pygame.K_ESCAPE]:
                running = False

            if player_slot == 1:
                move_x, move_y = get_direction(
                    keys, pygame.K_a, pygame.K_d, pygame.K_w, pygame.K_s
                )
                attack_key = pygame.K_f
            else:
                move_x, move_y = get_direction(
                    keys, pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN
                )
                attack_key = pygame.K_k

            buttons = int(Button.ATTACK) if keys[attack_key] else 0
            if keys[pygame.K_r]:
                buttons |= int(Button.REMATCH)
            client.set_input(move_x, move_y, buttons)

            own = client.get_entity()
            remote = next(iter(client.get_remote_entities()), None)
            winner = winner_id(own, remote)
            if own is None:
                status = "CONNECTING..."
            elif remote is None:
                status = "WAITING FOR OPPONENT"
            elif winner == -1:
                status = "DRAW"
            elif winner == own.entity_id:
                status = f"PLAYER {own.entity_id} WINS"
            elif winner == remote.entity_id:
                status = f"PLAYER {remote.entity_id} WINS"
            else:
                status = "FIGHT!"

            own_face = 1 if own is None or remote is None or remote.x >= own.x else -1
            remote_face = (
                1
                if remote is not None and own is not None and own.x >= remote.x
                else -1
            )

            screen.fill((18, 22, 38))
            pygame.draw.rect(
                screen,
                (42, 48, 78),
                (0, GROUND_Y, WIDTH, HEIGHT - GROUND_Y),
            )
            pygame.draw.line(
                screen,
                (95, 109, 160),
                (0, GROUND_Y),
                (WIDTH, GROUND_Y),
                3,
            )
            for offset in range(-200, WIDTH + 200, 80):
                pygame.draw.circle(screen, (30, 35, 58), (offset, GROUND_Y + 45), 36)

            _draw_health_bars(screen, font, own, remote)
            for entity, face in ((own, own_face), (remote, remote_face)):
                if entity is None:
                    continue
                sx, sy = world_to_screen(entity.x, entity.y)
                draw_fighter(
                    screen,
                    sx,
                    sy,
                    entity_color(entity.entity_id),
                    bool(entity.flags & FLAG_ATTACKING),
                    face,
                    bool(entity.flags & FLAG_DEAD),
                )

            title = font.render(status, True, (255, 226, 112))
            screen.blit(title, title.get_rect(center=(WIDTH // 2, 85)))
            mode = small_font.render(
                f"C2: remote snapshots buffered {interpolation_delay * 1000:.0f} ms",
                True,
                (150, 160, 200),
            )
            screen.blit(mode, mode.get_rect(center=(WIDTH // 2, 112)))
            controls = small_font.render(
                f"P{player_slot}: "
                + ("WASD + F" if player_slot == 1 else "ARROWS + K")
                + "    R: Rematch    ESC: Quit",
                True,
                (210, 214, 230),
            )
            screen.blit(controls, controls.get_rect(center=(WIDTH // 2, HEIGHT - 18)))

            if winner is not None:
                pygame.draw.rect(
                    screen,
                    (10, 12, 24),
                    (WIDTH // 2 - 260, HEIGHT // 2 - 120, 520, 220),
                    border_radius=18,
                )
                pygame.draw.rect(
                    screen,
                    (255, 226, 112),
                    (WIDTH // 2 - 260, HEIGHT // 2 - 120, 520, 220),
                    width=4,
                    border_radius=18,
                )
                game_over = font.render(
                    "K.O." if winner != -1 else "TIME", True, (255, 226, 112)
                )
                winner_text = font.render(status, True, (255, 255, 255))
                restart_text = small_font.render(
                    "Press R to rematch or ESC to quit", True, (210, 214, 230)
                )
                screen.blit(
                    game_over,
                    game_over.get_rect(center=(WIDTH // 2, HEIGHT // 2 - 65)),
                )
                screen.blit(
                    winner_text,
                    winner_text.get_rect(center=(WIDTH // 2, HEIGHT // 2)),
                )
                screen.blit(
                    restart_text,
                    restart_text.get_rect(center=(WIDTH // 2, HEIGHT // 2 + 65)),
                )

            pygame.display.flip()
            await asyncio.sleep(0)
    finally:
        client_task.cancel()
        await asyncio.gather(client_task, return_exceptions=True)
        pygame.quit()


def main() -> None:
    parser = argparse.ArgumentParser(prog="pygame-fighter-c2")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--player", type=int, choices=(1, 2), required=True)
    parser.add_argument("--tick-rate", type=float, default=30.0)
    parser.add_argument(
        "--interpolation-delay-ms",
        type=float,
        default=100.0,
        help="how far behind the estimated server timeline to render the opponent",
    )
    args = parser.parse_args()
    if args.interpolation_delay_ms < 0:
        parser.error("--interpolation-delay-ms must be non-negative")

    asyncio.run(
        run_game(
            args.host,
            args.port,
            args.player,
            args.tick_rate,
            args.interpolation_delay_ms / 1000.0,
        )
    )


if __name__ == "__main__":
    main()
