from __future__ import annotations

from dataclasses import dataclass


class RectangleError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    def __post_init__(self) -> None:
        if any(type(value) is not int for value in (self.left, self.top, self.right, self.bottom)):
            raise RectangleError('rectangle coordinates must be integers')

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def area(self) -> int:
        return self.width * self.height

    def require_inside(self, bounds: Rect) -> None:
        if not (
            bounds.left <= self.left < self.right <= bounds.right
            and bounds.top <= self.top < self.bottom <= bounds.bottom
        ):
            raise RectangleError(f'expected a nonempty rectangle inside {bounds}, got {self}')

    def intersection(self, other: Rect) -> Rect | None:
        left, top = max(self.left, other.left), max(self.top, other.top)
        right, bottom = min(self.right, other.right), min(self.bottom, other.bottom)
        if left >= right or top >= bottom:
            return None
        return Rect(left, top, right, bottom)
