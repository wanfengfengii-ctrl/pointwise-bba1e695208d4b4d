from __future__ import annotations

from dataclasses import dataclass

from registration.geometry import Rect


@dataclass(frozen=True, slots=True)
class Origin:
    frame_no: int
    block_index: int
    x: int
    y: int

    def shifted(self, pixels: int) -> Origin:
        return Origin(self.frame_no, self.block_index, self.x + pixels, self.y)


@dataclass(frozen=True, slots=True)
class MoveEvidence:
    """The most recent rectangle move that sampled a screen position.

    ``x`` and ``y`` are the absolute screen coordinates that the move command
    actually sampled, not coordinates inside a device block.
    """

    frame_no: int
    command_index: int
    x: int
    y: int


def origin_at(frame_no: int, block_index: int, rect: Rect, screen_x: int, screen_y: int) -> Origin:
    return Origin(frame_no, block_index, screen_x - rect.left, screen_y - rect.top)
