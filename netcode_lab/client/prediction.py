"""Client-side prediction and reconciliation (C1).

This module is pure logic with no networking, so it can be unit tested on its
own. ``PredictedClient`` in ``predicted_client.py`` wires it to the network.

How it works
------------
1. Every time the client sends an INPUT it also applies that input locally,
   advancing its own copy of the player by exactly one server tick. The input
   is kept in a "pending" list because the server has not confirmed it yet.
2. When a SNAPSHOT arrives, the client learns the newest input the server has
   applied. It then:
     a. drops every pending input up to and including that one,
     b. resets its position to the server's authoritative position,
     c. replays the inputs that are still pending, one tick each.
3. The distance between where the client *was* drawing the player and where it
   ends up after the replay is the correction. Its size and how often it
   happens are the C1 metrics in the proposal.

The movement rule in ``step`` must stay identical to ``World.tick`` on the
server (same speed, same diagonal normalisation, same clamping). If the two
ever differ, every snapshot will cause a correction.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

SEQ_MASK = 0xFFFFFFFF
# Server positions are rounded to whole units on the wire, so a difference
# below this is rounding noise, not a real correction.
DEFAULT_CORRECTION_THRESHOLD = 1.0


def seq_newer(a: int, b: int) -> bool:
    """True if 32-bit sequence ``a`` is newer than ``b`` (handles wrap-around)."""
    return a != b and ((a - b) & SEQ_MASK) < 0x80000000


@dataclass(frozen=True)
class PendingInput:
    input_seq: int
    move_x: int
    move_y: int


@dataclass
class CorrectionStats:
    reconciliations: int = 0   # snapshots used for reconciliation
    corrections: int = 0       # reconciliations that moved the player > threshold
    total_px: float = 0.0      # sum of correction sizes (only counted corrections)
    max_px: float = 0.0
    last_px: float = 0.0       # size of the most recent reconciliation (any size)

    @property
    def mean_px(self) -> float:
        return self.total_px / self.corrections if self.corrections else 0.0

    @property
    def rate(self) -> float:
        """Fraction of reconciliations that produced a visible correction."""
        return self.corrections / self.reconciliations if self.reconciliations else 0.0


@dataclass
class Predictor:
    world_width: float
    world_height: float
    player_speed: float
    tick_dt: float
    correction_threshold: float = DEFAULT_CORRECTION_THRESHOLD

    x: float = 0.0
    y: float = 0.0
    vx: float = 0.0
    vy: float = 0.0
    initialized: bool = False
    last_acked_input: int = 0
    pending: deque[PendingInput] = field(default_factory=deque)
    _acked_move: tuple[int, int] = (0, 0)
    stats: CorrectionStats = field(default_factory=CorrectionStats)

    # -- movement (mirror of server World.tick) ---------------------------------

    def step(self, move_x: int, move_y: int) -> None:
        mx = max(-1, min(1, int(move_x)))
        my = max(-1, min(1, int(move_y)))
        if mx == 0 and my == 0:
            self.vx = 0.0
            self.vy = 0.0
            return
        length = math.hypot(mx, my)
        self.vx = (mx / length) * self.player_speed
        self.vy = (my / length) * self.player_speed
        self.x = _clamp(self.x + self.vx * self.tick_dt, 0.0, self.world_width)
        self.y = _clamp(self.y + self.vy * self.tick_dt, 0.0, self.world_height)

    # -- prediction ----------------------------------------------------------

    def apply_local_input(self, input_seq: int, move_x: int, move_y: int) -> None:
        """Record an input that was just sent and predict its effect immediately."""
        self.pending.append(PendingInput(input_seq, move_x, move_y))
        if self.initialized:
            self.step(move_x, move_y)

    # -- reconciliation ------------------------------------------------------

    def reconcile(
        self,
        server_x: float,
        server_y: float,
        acked_input_seq: int,
        server_vx: float = 0.0,
        server_vy: float = 0.0,
    ) -> float:
        """Adopt the server state, replay unacknowledged inputs.

        Returns the size of the correction in world units (0.0 on the very
        first snapshot, which just initialises the prediction).

        Tick-phase ambiguity: the server applies an input as soon as it
        arrives but moves players on its own independent tick timer. So when
        a snapshot says "input k has been received", the server may have run
        0, 1 or (under jitter) 2 ticks with k so far, while the prediction
        assumed exactly 1. That is not a real disagreement, just "the server
        hasn't caught up yet / ran slightly ahead". So we build the replay for
        each of those cases and keep the one closest to what we were already
        showing. A genuine disagreement (lost input, server rule) matches
        none of them and is still counted as a correction.
        """
        if acked_input_seq and (
            self.last_acked_input == 0 or seq_newer(acked_input_seq, self.last_acked_input)
        ):
            self.last_acked_input = acked_input_seq
        while self.pending and not seq_newer(self.pending[0].input_seq, self.last_acked_input):
            item = self.pending.popleft()
            self._acked_move = (item.move_x, item.move_y)

        before_x, before_y = self.x, self.y
        was_initialized = self.initialized

        # extra ticks of the newest acknowledged input still to come (+1),
        # already done (0, the textbook case) or one too many (-1).
        phases = (0, 1, -1) if was_initialized and self._acked_move != (0, 0) else (0,)
        best: tuple[float, float, float, float, float] | None = None
        textbook: tuple[float, float, float, float, float] | None = None
        for phase in phases:
            self.x, self.y = float(server_x), float(server_y)
            self.vx, self.vy = float(server_vx), float(server_vy)
            if phase == 1:
                self.step(*self._acked_move)
            elif phase == -1:
                self.step(-self._acked_move[0], -self._acked_move[1])
            for item in self.pending:
                self.step(item.move_x, item.move_y)
            error = math.hypot(self.x - before_x, self.y - before_y)
            result = (error, self.x, self.y, self.vx, self.vy)
            if phase == 0:
                textbook = result
            if best is None or error < best[0] - 1e-9:
                best = result
        # No timing explanation fits: it is a real disagreement, so use the
        # standard replay and report its full size.
        if best[0] > self.correction_threshold:
            best = textbook
        error, self.x, self.y, self.vx, self.vy = best
        if not self.pending and self._acked_move == (0, 0):
            self.vx = self.vy = 0.0
        self.initialized = True

        if not was_initialized:
            return 0.0

        self.stats.reconciliations += 1
        self.stats.last_px = error
        if error > self.correction_threshold:
            self.stats.corrections += 1
            self.stats.total_px += error
            self.stats.max_px = max(self.stats.max_px, error)
        return error


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
