from __future__ import annotations

import argparse
import asyncio

import pygame

from netcode_lab.client.client import ClientConfig, GameClient
from netcode_lab.client.predicted_client import PredictedClient
from netcode_lab.config import ServerConfig
from netcode_lab.protocol.messages import (
    FLAG_ATTACKING,
    FLAG_DEAD,
    Button,
    EntityState,
)

WIDTH = 960
HEIGHT = 540
WORLD_WIDTH = 1024
WORLD_HEIGHT = 768
GROUND_Y = 430

PLAYER_ONE = (74, 180, 255)
PLAYER_TWO = (255, 82, 82)


def world_to_screen(x: float, y: float) -> tuple[int, int]:
    return int(x / WORLD_WIDTH * WIDTH), GROUND_Y - int(y / WORLD_HEIGHT * 180)


def get_direction(
    keys: pygame.key.ScancodeWrapper,
    left: int,
    right: int,
    up: int,
    down: int,
) -> tuple[int, int]:
    move_x = int(keys[right]) - int(keys[left])
    move_y = int(keys[up]) - int(keys[down])
    return move_x, move_y


def make_client(mode: str, host: str, port: int, tick_rate: float, player_slot: int) -> GameClient:
    config = ClientConfig(
        host=host,
        port=port,
        input_rate=tick_rate,
        player_slot=player_slot,
    )
    if mode == "c1":
        return PredictedClient(config, ServerConfig(tick_rate=tick_rate))
    return GameClient(config)


def remote_entity(client: GameClient) -> EntityState | None:
    snapshot = client.latest_snapshot
    if snapshot is None or client.player_id is None:
        return None
    return next(
        (entity for entity in snapshot.entities if entity.entity_id != client.player_id),
        None,
    )


def winner_id(own: EntityState | None, remote: EntityState | None) -> int | None:
    if own is None or remote is None:
        return None
    if own.health <= 0 and remote.health <= 0:
        return -1
    if own.health <= 0:
        return remote.entity_id
    if remote.health <= 0:
        return own.entity_id
    return None


def entity_color(entity_id: int) -> tuple[int, int, int]:
    return PLAYER_ONE if entity_id % 2 == 1 else PLAYER_TWO


async def run_game(host: str, port: int, mode: str, tick_rate: float, player_slot: int) -> None:
    pygame.init()
    pygame.display.set_caption(
        f"NetCode Lab - Player {player_slot} ({mode.upper()}, server authoritative)"
    )
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("consolas", 22)
    small_font = pygame.font.SysFont("consolas", 16)

    client = make_client(mode, host, port, tick_rate, player_slot)
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
                running = False
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
            remote = remote_entity(client)
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

            if own is not None and remote is not None:
                own_face = 1 if remote.x >= own.x else -1
                remote_face = 1 if own.x >= remote.x else -1
            else:
                own_face = 1
                remote_face = -1

            screen.fill((18, 22, 38))

            pygame.draw.rect(screen, (42, 48, 78), (0, GROUND_Y, WIDTH, HEIGHT - GROUND_Y))
            pygame.draw.line(screen, (95, 109, 160), (0, GROUND_Y), (WIDTH, GROUND_Y), 3)

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
            mode_text = small_font.render(
                f"MODE {mode.upper()}"
                + (" (prediction)" if mode == "c1" else " (server state)")
                + f"   PLAYER {player_slot}",
                True,
                (150, 160, 200),
            )
            screen.blit(mode_text, mode_text.get_rect(center=(WIDTH // 2, 112)))
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
                game_over = font.render("K.O." if winner != -1 else "TIME", True, (255, 226, 112))
                winner_text = font.render(status, True, (255, 255, 255))
                restart_text = small_font.render(
                    "Press R to rematch or ESC to quit", True, (210, 214, 230)
                )
                screen.blit(game_over, game_over.get_rect(center=(WIDTH // 2, HEIGHT // 2 - 65)))
                screen.blit(winner_text, winner_text.get_rect(center=(WIDTH // 2, HEIGHT // 2)))
                screen.blit(restart_text, restart_text.get_rect(center=(WIDTH // 2, HEIGHT // 2 + 65)))

            pygame.display.flip()
            await asyncio.sleep(0)
    finally:
        client_task.cancel()
        await asyncio.gather(client_task, return_exceptions=True)
        pygame.quit()


def _draw_health_bars(
    screen: pygame.Surface,
    font: pygame.font.Font,
    own: EntityState | None,
    remote: EntityState | None,
) -> None:
    if own is not None and remote is not None:
        left, right = (own, remote) if own.entity_id < remote.entity_id else (remote, own)
        draw_health_bar(
            screen, font, 40, 24, f"PLAYER {left.entity_id}",
            left.health, entity_color(left.entity_id),
        )
        draw_health_bar(
            screen, font, WIDTH - 360, 24, f"PLAYER {right.entity_id}",
            right.health, entity_color(right.entity_id),
        )
    elif own is not None:
        draw_health_bar(
            screen, font, 40, 24, f"PLAYER {own.entity_id}",
            own.health, entity_color(own.entity_id),
        )


def draw_health_bar(
    screen: pygame.Surface,
    font: pygame.font.Font,
    x: int,
    y: int,
    label: str,
    health: int,
    color: tuple[int, int, int],
) -> None:
    health = max(0, min(100, int(health)))
    label_surface = font.render(label, True, (230, 232, 242))
    screen.blit(label_surface, (x, y))
    pygame.draw.rect(screen, (55, 58, 72), (x, y + 32, 320, 24), border_radius=8)
    pygame.draw.rect(screen, color, (x, y + 32, 320 * health // 100, 24), border_radius=8)


def draw_fighter(
    screen: pygame.Surface,
    x: int,
    foot_y: int,
    color: tuple[int, int, int],
    attacking: bool,
    face: int = 1,
    dead: bool = False,
) -> None:
    if dead:
        color = (90, 94, 110)

    pygame.draw.circle(screen, color, (x, foot_y - 118), 24)
    pygame.draw.rect(screen, color, (x - 25, foot_y - 96, 50, 66), border_radius=12)
    pygame.draw.line(screen, color, (x - 20, foot_y - 38), (x - 38, foot_y), 10)
    pygame.draw.line(screen, color, (x + 20, foot_y - 38), (x + 38, foot_y), 10)

    arm_end_x = x + (58 if attacking else 38) * face
    arm_end_y = foot_y - 78 if attacking else foot_y - 68
    pygame.draw.line(
        screen,
        color,
        (x + 20 * face, foot_y - 82),
        (arm_end_x, arm_end_y),
        11,
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="pygame-fighter")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument(
        "--player",
        type=int,
        choices=(1, 2),
        required=True,
        help="which player this window controls (1 or 2)",
    )
    parser.add_argument(
        "--mode",
        choices=("c0", "c1"),
        default="c0",
        help="c0 = show server state, c1 = client-side prediction",
    )
    parser.add_argument(
        "--tick-rate",
        type=float,
        default=30.0,
        help="server tick rate; c1 must match the server's --tick-rate",
    )
    args = parser.parse_args()

    asyncio.run(run_game(args.host, args.port, args.mode, args.tick_rate, args.player))


if __name__ == "__main__":
    main()
