from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from struct import Struct

from registration.geometry import Rect

from .origins import Origin


TILE_EDGE = 128
_PIXEL_COUNT = TILE_EDGE * TILE_EDGE
# Each source run gives the tile interval and the raw position of its first pixel.
_RUN = Struct('<HHqQQQ')
_ROW_LENGTH = Struct('<H')


class TileEvidenceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class TileKey:
    x: int
    y: int

    @property
    def bounds(self) -> Rect:
        left, top = self.x * TILE_EDGE, self.y * TILE_EDGE
        return Rect(left, top, left + TILE_EDGE, top + TILE_EDGE)


class Tile:
    __slots__ = ('_pixels', '_rows', '_source_bytes')

    def __init__(self) -> None:
        self._pixels = bytearray(_PIXEL_COUNT)
        self._rows = [b''] * TILE_EDGE
        self._source_bytes = 0

    @property
    def resident_bytes(self) -> int:
        # Include space for the row list, byte-string headers, and cache entries.
        return _PIXEL_COUNT + self._source_bytes + TILE_EDGE * 64 + 512

    @staticmethod
    def _span_offset(x: int, y: int, count: int) -> int:
        if not (0 <= y < TILE_EDGE and 0 <= x < x + count <= TILE_EDGE):
            raise ValueError('span must be nonempty and contained in one tile row')
        return y * TILE_EDGE + x

    def write_span(self, x: int, y: int, data: bytes, origin: Origin) -> None:
        count = len(data)
        offset = self._span_offset(x, y, count)
        end = x + count
        old_row = self._rows[y]
        new_run = (x, count, origin.frame_no, origin.block_index, origin.x, origin.y)
        if old_row:
            runs = [new_run]
            for start, size, frame, block, source_x, source_y in _RUN.iter_unpack(old_row):
                stop = start + size
                if stop <= x or start >= end:
                    runs.append((start, size, frame, block, source_x, source_y))
                    continue
                if start < x:
                    runs.append((start, x - start, frame, block, source_x, source_y))
                if stop > end:
                    runs.append((end, stop - end, frame, block, source_x + end - start, source_y))
            runs.sort()
            new_row = b''.join(_RUN.pack(*run) for run in runs)
        else:
            new_row = _RUN.pack(*new_run)
        self._pixels[offset:offset + count] = data
        self._rows[y] = new_row
        self._source_bytes += len(new_row) - len(old_row)

    def read_span(self, x: int, y: int, count: int) -> tuple[bytes, tuple[Origin, ...]]:
        offset = self._span_offset(x, y, count)
        end = x + count
        origins = []
        for start, size, frame, block, source_x, source_y in _RUN.iter_unpack(self._rows[y]):
            if start >= end:
                break
            low = max(x, start)
            high = min(end, start + size)
            if low < high:
                if low != x + len(origins):
                    raise TileEvidenceError('tile span has incomplete source coverage')
                origins.extend(Origin(frame, block, source_x + pos - start, source_y) for pos in range(low, high))
        if len(origins) != count:
            raise TileEvidenceError('tile span has incomplete source coverage')
        return bytes(self._pixels[offset:offset + count]), tuple(origins)

    def to_bytes(self) -> bytes:
        encoded = bytearray(self._pixels)
        for row in self._rows:
            encoded.extend(_ROW_LENGTH.pack(len(row)))
            encoded.extend(row)
        return bytes(encoded)

    @classmethod
    def from_bytes(cls, data: bytes) -> Tile:
        if len(data) < _PIXEL_COUNT + TILE_EDGE * _ROW_LENGTH.size:
            raise TileEvidenceError('incomplete tile revision')
        tile = cls()
        tile._pixels[:] = data[:_PIXEL_COUNT]
        position = _PIXEL_COUNT
        for y in range(TILE_EDGE):
            if position + _ROW_LENGTH.size > len(data):
                raise TileEvidenceError('incomplete tile row length')
            size, = _ROW_LENGTH.unpack_from(data, position)
            position += _ROW_LENGTH.size
            if size > TILE_EDGE * _RUN.size or size % _RUN.size or position + size > len(data):
                raise TileEvidenceError('invalid source runs in tile row')
            tile._rows[y] = data[position:position + size]
            tile._source_bytes += size
            position += size
        if position != len(data):
            raise TileEvidenceError('extra bytes in tile revision')
        return tile


def tile_intersections(rect: Rect) -> Iterator[tuple[TileKey, Rect]]:
    if rect.width <= 0 or rect.height <= 0:
        return
    for tile_y in range(rect.top // TILE_EDGE, (rect.bottom - 1) // TILE_EDGE + 1):
        for tile_x in range(rect.left // TILE_EDGE, (rect.right - 1) // TILE_EDGE + 1):
            key = TileKey(tile_x, tile_y)
            overlap = rect.intersection(key.bounds)
            if overlap is not None:
                yield key, overlap
