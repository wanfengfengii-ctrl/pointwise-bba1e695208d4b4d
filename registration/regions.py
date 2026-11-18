from collections.abc import Mapping
from dataclasses import dataclass

from .geometry import Rect


class UnknownRegion(LookupError):
    pass


@dataclass(frozen=True, slots=True, init=False)
class NamedRegions:
    _entries: tuple[tuple[str, Rect], ...]

    def __init__(self, regions: Mapping[str, Rect]) -> None:
        items = tuple(regions.items())
        for name, rect in items:
            if not isinstance(name, str) or not name:
                raise ValueError('region names must be nonempty strings')
            if not isinstance(rect, Rect):
                raise TypeError(f'region {name!r} needs a Rect')
        object.__setattr__(self, '_entries', tuple(sorted(items, key=lambda item: item[0])))

    @property
    def entries(self) -> tuple[tuple[str, Rect], ...]:
        return self._entries

    def resolve(self, name: str) -> Rect:
        for entry_name, rect in self._entries:
            if name == entry_name:
                return rect
        raise UnknownRegion(name)
