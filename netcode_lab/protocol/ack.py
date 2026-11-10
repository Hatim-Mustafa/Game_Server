from __future__ import annotations

SEQ_MASK = 0xFFFFFFFF
WINDOW_BITS = 32


class AckWindow:
    def __init__(self) -> None:
        self.highest: int = 0
        self.bits: int = 0

    def mark(self, seq: int) -> None:
        seq &= SEQ_MASK
        if seq == 0:
            return
        if seq > self.highest:
            if self.highest == 0:
                self.highest = seq
                self.bits = 0
                return
            shift = seq - self.highest
            if shift >= WINDOW_BITS:
                self.bits = 0
            else:
                self.bits = ((self.bits << shift) | (1 << (shift - 1))) & SEQ_MASK
            self.highest = seq
            return
        offset = self.highest - seq
        if 1 <= offset <= WINDOW_BITS:
            self.bits |= 1 << (offset - 1)

    def build(self) -> tuple[int, int]:
        return self.highest, self.bits & SEQ_MASK
