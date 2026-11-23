from __future__ import annotations

import argparse
import asyncio
import math

import pygame

from netcode_lab.client.client import ClientConfig, GameClient
from netcode_lab.config import ServerConfig


WIDTH = 960
HEIGHT = 540
WORLD_WIDTH = 1024
WORLD_HEIGHT = 768
GROUND_Y = 430
FIGHT_DISTANCE = 90


def world_to_screen(x: float, y: float) -> tuple[int, int]:
    return int(x / WORLD_WIDTH * WIDTH), GROUND_Y - int(y / WORLD_HEIGHT * 180)


def get_direction(keys: pygame.key.ScancodeWrapper, left: int, right: int, up: int, down: int) -> tuple[int, int]:
    move_x = int(keys[right]) - int(keys[left])
    move_y = int(keys[up]) - int(keys[down])
    return move_x, move_y


async def run_game(host: str, port: int) -> None:
    pygame.init()
    pygame.display.set_caption("NetCode Lab - 1v1 Fighter")
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("consolas", 22)
    small_font = pygame.font.SysFont("consolas", 16)

    first = GameClient(ClientConfig(host=host, port=port, input_rate=30))
    second = GameClient(ClientConfig(host=host, port=port, input_rate=30))
    first_task = asyncio.create_task(first.run())
    second_task = asyncio.create_task(second.run())

    health = {1: 100, 2: 100}
    attack_timer = {1: 0.0, 2: 0.0}
    attack_flash = {1: 0.0, 2: 0.0}
    punch_down = {1: False, 2: False}
    hit_pending = {1: False, 2: False}
    facing = {1: 1, 2: -1}
    winner: int | None = None
    running = True

    try:
        while running:
            dt = clock.tick(60) / 1000.0

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                if event.type == pygame.KEYDOWN and winner is not None and event.key == pygame.K_r:
                    health = {1: 100, 2: 100}
                    attack_timer = {1: 0.0, 2: 0.0}
                    attack_flash = {1: 0.0, 2: 0.0}
                    punch_down = {1: False, 2: False}
                    hit_pending = {1: False, 2: False}
                    facing = {1: 1, 2: -1}
                    winner = None

            keys = pygame.key.get_pressed()
            if winner is None:
                first_move_x, first_move_y = get_direction(keys, pygame.K_a, pygame.K_d, pygame.K_w, pygame.K_s)
                second_move_x, second_move_y = get_direction(keys, pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN)
                if first_move_x != 0:
                    facing[1] = first_move_x
                if second_move_x != 0:
                    facing[2] = second_move_x
                first.set_input(first_move_x, first_move_y)
                second.set_input(second_move_x, second_move_y)
            else:
                first.set_input(0, 0)
                second.set_input(0, 0)

            punch_keys = {
                1: keys[pygame.K_f],
                2: keys[pygame.K_k],
            }
            for player_id in (1, 2):
                attack_timer[player_id] = max(0.0, attack_timer[player_id] - dt)
                attack_flash[player_id] = max(0.0, attack_flash[player_id] - dt)
                if (
                    winner is None
                    and health[player_id] > 0
                    and punch_keys[player_id]
                    and not punch_down[player_id]
                    and attack_timer[player_id] == 0
                ):
                    attack_timer[player_id] = 0.55
                    attack_flash[player_id] = 0.18
                    hit_pending[player_id] = True
                    punch_down[player_id] = True
                if not punch_keys[player_id]:
                    punch_down[player_id] = False

            first_entity = first.get_entity()
            second_entity = second.get_entity()

            if first_entity is not None and second_entity is not None:
                distance = math.hypot(first_entity.x - second_entity.x, first_entity.y - second_entity.y)
                player1_face = 1 if second_entity.x >= first_entity.x else -1
                player2_face = 1 if first_entity.x >= second_entity.x else -1
                facing[1] = player1_face
                facing[2] = player2_face
                dx = second_entity.x - first_entity.x

                if winner is None and distance <= FIGHT_DISTANCE:
                    if (
                        health[1] > 0
                        and health[2] > 0
                        and attack_flash[1] > 0.12
                        and hit_pending[1]
                    ):
                        health[2] = max(0, health[2] - 10)
                        hit_pending[1] = False
                    if (
                        health[1] > 0
                        and health[2] > 0
                        and attack_flash[2] > 0.12
                        and hit_pending[2]
                    ):
                        health[1] = max(0, health[1] - 10)
                        hit_pending[2] = False

            if health[1] <= 0:
                winner = 2
            elif health[2] <= 0:
                winner = 1

            if winner is not None:
                status = f"PLAYER {winner} WINS"
            else:
                status = "FIGHT!"

            screen.fill((18, 22, 38))

            pygame.draw.rect(screen, (42, 48, 78), (0, GROUND_Y, WIDTH, HEIGHT - GROUND_Y))
            pygame.draw.line(screen, (95, 109, 160), (0, GROUND_Y), (WIDTH, GROUND_Y), 3)

            for offset in range(-200, WIDTH + 200, 80):
                pygame.draw.circle(screen, (30, 35, 58), (offset, GROUND_Y + 45), 36)

            draw_health_bar(screen, font, 40, 24, "PLAYER 1", health[1], (74, 180, 255))
            draw_health_bar(screen, font, WIDTH - 360, 24, "PLAYER 2", health[2], (255, 82, 82))

            if first_entity is not None:
                sx, sy = world_to_screen(first_entity.x, first_entity.y)
                first_face = 1
                if second_entity is not None:
                    first_face = 1 if second_entity.x >= first_entity.x else -1
                draw_fighter(screen, sx, sy, (74, 180, 255), attack_flash[1] > 0, first_face)
            if second_entity is not None:
                sx, sy = world_to_screen(second_entity.x, second_entity.y)
                second_face = 1
                if first_entity is not None:
                    second_face = 1 if first_entity.x >= second_entity.x else -1
                draw_fighter(screen, sx, sy, (255, 82, 82), attack_flash[2] > 0, second_face)

            title = font.render(status, True, (255, 226, 112))
            screen.blit(title, title.get_rect(center=(WIDTH // 2, 85)))
            controls = small_font.render(
                "P1: WASD + F    P2: ARROWS + K    ESC: Quit",
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
                game_over = font.render("K.O.", True, (255, 226, 112))
                winner_text = font.render(f"PLAYER {winner} WINS", True, (255, 255, 255))
                restart_text = small_font.render("Press R to rematch or ESC to quit", True, (210, 214, 230))
                screen.blit(game_over, game_over.get_rect(center=(WIDTH // 2, HEIGHT // 2 - 65)))
                screen.blit(winner_text, winner_text.get_rect(center=(WIDTH // 2, HEIGHT // 2)))
                screen.blit(restart_text, restart_text.get_rect(center=(WIDTH // 2, HEIGHT // 2 + 65)))

            if keys[pygame.K_ESCAPE]:
                running = False

            pygame.display.flip()
            await asyncio.sleep(0)
    finally:
        for client_task in (first_task, second_task):
            client_task.cancel()
        await asyncio.gather(first_task, second_task, return_exceptions=True)
        pygame.quit()


def draw_health_bar(
    screen: pygame.Surface,
    font: pygame.font.Font,
    x: int,
    y: int,
    label: str,
    health: int,
    color: tuple[int, int, int],
) -> None:
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
) -> None:
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
    args = parser.parse_args()

    asyncio.run(run_game(args.host, args.port))


if __name__ == "__main__":
    main()
