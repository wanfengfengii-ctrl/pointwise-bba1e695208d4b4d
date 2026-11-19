from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from registration.geometry import Rect, RectangleError
from registration.specification import ScreenSpec


class BaseFrameError(ValueError):
    pass


class FrameModeError(ValueError):
    pass


class BlockBody(Protocol):
    def read(self, size: int, /) -> bytes: ...


@dataclass(frozen=True, slots=True)
class FrameHeader:
    frame_no: int
    mode: str = 'permanent'

    def require_permanent(self) -> None:
        if self.mode != 'permanent':
            raise FrameModeError(f'unsupported V1 frame mode {self.mode!r}')


@dataclass(frozen=True, slots=True)
class DeviceBlock:
    rect: Rect
    body: BlockBody


@dataclass(frozen=True, slots=True)
class MoveRect:
    """An in-screen rectangle move from ``source`` to an equal-sized ``target``."""

    source: Rect
    target: Rect


@dataclass(frozen=True, slots=True)
class DeviceFrame:
    header: FrameHeader
    blocks: Iterable[DeviceBlock]


def validate_block_rectangle(block: DeviceBlock, screen: ScreenSpec) -> None:
    if not isinstance(block.rect, Rect):
        raise RectangleError('device block needs a Rect')
    block.rect.require_inside(screen.bounds)


def validate_move_rectangle(move: MoveRect, screen: ScreenSpec) -> None:
    if not isinstance(move.source, Rect) or not isinstance(move.target, Rect):
        raise RectangleError('move needs source and target Rect values')
    move.source.require_inside(screen.bounds)
    move.target.require_inside(screen.bounds)
    if move.source.width != move.target.width or move.source.height != move.target.height:
        raise RectangleError('move source and target must have equal width and height')


def validate_base_block(block: DeviceBlock, screen: ScreenSpec, block_index: int) -> None:
    if block_index != 0 or block.rect != screen.bounds:
        raise BaseFrameError('first frame needs exactly one full-screen block')
