from dataclasses import dataclass

from .geometry import Rect
from .identity import RecordingIdentity
from .regions import NamedRegions


def is_storage_integer(value: object) -> bool:
    return type(value) is int and -(1 << 63) <= value < (1 << 63)


@dataclass(frozen=True, slots=True)
class ScreenSpec:
    width: int
    height: int
    first_frame: int

    def __post_init__(self) -> None:
        for size in (self.width, self.height):
            if not is_storage_integer(size) or size <= 0:
                raise ValueError('screen dimensions must be positive SQLite integers')
        if not is_storage_integer(self.first_frame):
            raise ValueError('first frame must be a SQLite integer')

    @property
    def bounds(self) -> Rect:
        return Rect(0, 0, self.width, self.height)


@dataclass(frozen=True, slots=True)
class RecordingSpec:
    identity: RecordingIdentity
    screen: ScreenSpec
    regions: NamedRegions

    def __post_init__(self) -> None:
        bounds = self.screen.bounds
        for _, rect in self.regions.entries:
            rect.require_inside(bounds)
