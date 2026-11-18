from __future__ import annotations

from collections.abc import Callable, Iterator

from registration.specification import ScreenSpec

from .origins import Origin
from .staging import TileStaging
from .tiles import TILE_EDGE, Tile, TileKey


class ScreenDraft:
    def __init__(self, screen: ScreenSpec, load_base: Callable[[TileKey], Tile] | None) -> None:
        self._screen = screen
        self._load_base = load_base
        self._staging = TileStaging()

    def write_span(self, x: int, y: int, data: bytes, origin: Origin) -> None:
        if not (0 <= y < self._screen.height and 0 <= x < x + len(data) <= self._screen.width):
            raise ValueError('draft span must be nonempty and inside the screen')
        tile_y, local_y = divmod(y, TILE_EDGE)
        position = 0
        while position < len(data):
            tile_x, local_x = divmod(x + position, TILE_EDGE)
            count = min(TILE_EDGE - local_x, len(data) - position)
            key = TileKey(tile_x, tile_y)
            tile = self._staging.read(key)
            if tile is None:
                tile = Tile() if self._load_base is None else self._load_base(key)
            source = origin if position == 0 else origin.shifted(position)
            tile.write_span(local_x, local_y, data[position:position + count], source)
            self._staging.write(key, tile)
            position += count

    def iter_dirty_tiles(self) -> Iterator[tuple[TileKey, bytes]]:
        """Hand off the sealed candidate once and release staging before commit."""
        yield from self._staging.iter_changed()
        self.close()

    def close(self) -> None:
        self._staging.close()

    def __enter__(self) -> ScreenDraft:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
