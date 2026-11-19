from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from struct import Struct

from registration.geometry import Rect

from .origins import MoveEvidence, Origin


TILE_EDGE = 128
_PIXEL_COUNT = TILE_EDGE * TILE_EDGE
# Each source run gives the tile interval and the raw position of its first pixel.
_RUN = Struct('<HHqQQQ')
# Move runs add an explicit kind so direct writes can record an evidence clear.
# Fields: interval start, interval size, kind, reserved, frame, command, sample x, sample y.
_MOVE_RUN = Struct('<HHBBqQQQ')
_ROW_LENGTH = Struct('<H')
_MOVE_KIND_CLEAR = 0
_MOVE_KIND_SAMPLE = 1
# V2 tile revisions append this marker and one encoded run stream per tile row.
_MOVE_MARKER = b'MV01'


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


def _merge_runs(old_row: bytes, x: int, end: int, replacements: Sequence[tuple], *, is_move: bool) -> bytes:
    """Replace the [x, end) interval, keeping run tails outside it."""
    struct = _MOVE_RUN if is_move else _RUN
    runs = list(replacements)
    if old_row:
        for run in struct.iter_unpack(old_row):
            start, size = run[0], run[1]
            stop = start + size
            if stop <= x or start >= end:
                runs.append(run)
                continue
            if start < x:
                runs.append((start, x - start) + _shift_payload(run, 0))
            if stop > end:
                runs.append((end, stop - end) + _shift_payload(run, end - start))
    runs.sort(key=lambda run: run[0])
    return b''.join(struct.pack(*run) for run in runs)


def _shift_payload(run: tuple, offset: int) -> tuple:
    """Coordinate payload of a run, shifted by ``offset`` pixels along the run."""
    if len(run) == 6:
        _, _, frame, block, source_x, source_y = run
        return frame, block, source_x + offset, source_y
    _, _, kind, reserved, frame, command, sample_x, sample_y = run
    if kind == _MOVE_KIND_CLEAR:
        return kind, reserved, frame, command, sample_x, sample_y
    return kind, reserved, frame, command, sample_x + offset, sample_y


def _slice_origin_runs(row: bytes, src: Rect, dst_left: int) -> list[tuple]:
    """Clip origin runs to a source interval and re-base them on a destination.

    Block-local source coordinates are preserved; only the tile interval start
    is translated from the source column to the matching destination column.
    """
    out: list[tuple] = []
    covered = 0
    for start, size, frame, block, source_x, source_y in _RUN.iter_unpack(row):
        low = max(src.left, start)
        high = min(src.right, start + size)
        if low < high:
            if low != src.left + covered:
                raise TileEvidenceError('move source row has incomplete origin coverage')
            out.append((
                dst_left + covered,
                high - low,
                frame,
                block,
                source_x + (low - start),
                source_y,
            ))
            covered += high - low
    if covered != src.width:
        raise TileEvidenceError('move source row has incomplete origin coverage')
    return out


class Tile:
    __slots__ = ('_pixels', '_rows', '_source_bytes', '_move_rows', '_move_bytes')

    def __init__(self) -> None:
        self._pixels = bytearray(_PIXEL_COUNT)
        self._rows = [b''] * TILE_EDGE
        self._source_bytes = 0
        self._move_rows = [b''] * TILE_EDGE
        self._move_bytes = 0

    @property
    def resident_bytes(self) -> int:
        # Include space for the row lists, byte-string headers, and cache entries.
        return (
            _PIXEL_COUNT
            + self._source_bytes
            + self._move_bytes
            + TILE_EDGE * 64 * 2
            + 512
        )

    @staticmethod
    def _span_offset(x: int, y: int, count: int) -> int:
        if not (0 <= y < TILE_EDGE and 0 <= x < x + count <= TILE_EDGE):
            raise ValueError('span must be nonempty and contained in one tile row')
        return y * TILE_EDGE + x

    def write_span(self, x: int, y: int, data: bytes, origin: Origin) -> None:
        """Direct device write: replace grays and origins and clear move evidence."""
        count = len(data)
        offset = self._span_offset(x, y, count)
        end = x + count
        new_run = (x, count, origin.frame_no, origin.block_index, origin.x, origin.y)
        new_row = _merge_runs(self._rows[y], x, end, [new_run], is_move=False)
        old_move = self._move_rows[y]
        clear_run = (x, count, _MOVE_KIND_CLEAR, 0, 0, 0, 0, 0)
        new_move_row = _merge_runs(old_move, x, end, [clear_run], is_move=True)
        self._pixels[offset:offset + count] = data
        self._source_bytes += len(new_row) - len(self._rows[y])
        self._move_bytes += len(new_move_row) - len(old_move)
        self._rows[y] = new_row
        self._move_rows[y] = new_move_row

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

    def read_row_runs(self, y: int) -> tuple[bytes, list[tuple]]:
        """Full-row pixels plus the raw origin runs that cover the row."""
        runs = list(_RUN.iter_unpack(self._rows[y]))
        covered = sum(run[1] for run in runs)
        if covered != TILE_EDGE:
            raise TileEvidenceError('tile row has incomplete source coverage')
        return bytes(self._pixels[y * TILE_EDGE:(y + 1) * TILE_EDGE]), runs

    def read_clip_runs(self, x: int, y: int, count: int) -> tuple[bytes, list[tuple]]:
        """A within-tile row interval as pixels and origin runs, no per-pixel objects."""
        self._span_offset(x, y, count)
        end = x + count
        pixels = bytes(self._pixels[y * TILE_EDGE + x:y * TILE_EDGE + end])
        clipped: list[tuple] = []
        covered = 0
        for start, size, frame, block, source_x, source_y in _RUN.iter_unpack(self._rows[y]):
            if start >= end:
                break
            low = max(x, start)
            high = min(end, start + size)
            if low < high:
                if low != x + covered:
                    raise TileEvidenceError('tile span has incomplete source coverage')
                clipped.append((
                    low - x, high - low, frame, block, source_x + (low - start), source_y,
                ))
                covered += high - low
        if covered != count:
            raise TileEvidenceError('tile span has incomplete source coverage')
        return pixels, clipped

    def write_move_clip_runs(
        self,
        x: int,
        y: int,
        pixels: bytes,
        runs: Sequence[tuple],
        frame_no: int,
        command_index: int,
        sample_x: int,
        sample_y: int,
    ) -> None:
        """Move a partial tile row using pre-encoded runs, no per-pixel objects."""
        count = len(pixels)
        if sum(run[1] for run in runs) != count:
            raise TileEvidenceError('move clip runs must match the pixel count')
        end = x + count
        new_origin = _merge_runs(self._rows[y], x, end, runs, is_move=False)
        stamped = (x, count, _MOVE_KIND_SAMPLE, 0,
                   frame_no, command_index, sample_x, sample_y)
        new_move = _merge_runs(self._move_rows[y], x, end, [stamped], is_move=True)
        self._pixels[y * TILE_EDGE + x:y * TILE_EDGE + end] = pixels
        self._source_bytes += len(new_origin) - len(self._rows[y])
        self._move_bytes += len(new_move) - len(self._move_rows[y])
        self._rows[y] = new_origin
        self._move_rows[y] = new_move

    def read_move_span(self, x: int, y: int, count: int) -> tuple[MoveEvidence | None, ...]:
        self._span_offset(x, y, count)
        end = x + count
        # An empty row means the whole tile predates move tracking (V1 archives).
        if not self._move_rows[y]:
            return (None,) * count
        evidence: list[MoveEvidence | None] = []
        for start, size, kind, _reserved, frame, command, sample_x, sample_y in _MOVE_RUN.iter_unpack(self._move_rows[y]):
            if start >= end:
                break
            low = max(x, start)
            high = min(end, start + size)
            if low < high:
                if low != x + len(evidence):
                    raise TileEvidenceError('tile move span has incomplete coverage')
                if kind == _MOVE_KIND_CLEAR:
                    evidence.extend([None] * (high - low))
                elif kind == _MOVE_KIND_SAMPLE:
                    evidence.extend(
                        MoveEvidence(frame, command, sample_x + pos - start, sample_y)
                        for pos in range(low, high)
                    )
                else:
                    raise TileEvidenceError('unknown move evidence kind')
        if len(evidence) != count:
            raise TileEvidenceError('tile move span has incomplete coverage')
        return tuple(evidence)

    def shift_rect_in_tile(
        self,
        src: Rect,
        dst: Rect,
        frame_no: int,
        command_index: int,
    ) -> None:
        """Move an equal-sized in-tile rect, sampling the pre-write content.

        Coordinates are tile-local. Rows are visited away from the source band
        and horizontal intervals are shifted directionally, so every source
        pixel is consumed before any target can overwrite it. Grays move as a
        byte slice and the two run streams shift as whole runs, never building
        per-pixel objects.
        """
        dx = dst.left - src.left
        dy = dst.top - src.top
        row_numbers = range(dst.top, dst.bottom)
        if dy > 0:
            row_numbers = range(dst.bottom - 1, dst.top - 1, -1)
        for dst_y in row_numbers:
            src_y = dst_y - dy
            self._shift_row(src, dst, dx, src_y, dst_y, frame_no, command_index)

    def _shift_row(self, src, dst, dx, src_y, dst_y, frame_no, command_index) -> None:
        # 1) Grays: the source slice is fully read before the target write.
        pixels = bytes(self._pixels[src_y * TILE_EDGE + src.left:
                                    src_y * TILE_EDGE + src.right])
        self._pixels[dst_y * TILE_EDGE + dst.left:
                     dst_y * TILE_EDGE + dst.right] = pixels
        # 2) Block origins: copy the source runs, re-based onto dst.left.
        origin_runs = _slice_origin_runs(self._rows[src_y], src, dst.left)
        new_origin = _merge_runs(
            self._rows[dst_y], dst.left, dst.right, origin_runs, is_move=False
        )
        self._source_bytes += len(new_origin) - len(self._rows[dst_y])
        self._rows[dst_y] = new_origin
        # 3) Move evidence: this command replaces evidence over the whole dst,
        #    naming the absolute source cell each pixel was sampled from.
        stamped = (dst.left, dst.width, _MOVE_KIND_SAMPLE, 0,
                   frame_no, command_index, src.left, src_y)
        new_move = _merge_runs(
            self._move_rows[dst_y], dst.left, dst.right, [stamped], is_move=True
        )
        self._move_bytes += len(new_move) - len(self._move_rows[dst_y])
        self._move_rows[dst_y] = new_move

    def to_bytes(self) -> bytes:
        encoded = bytearray(self._pixels)
        for row in self._rows:
            encoded.extend(_ROW_LENGTH.pack(len(row)))
            encoded.extend(row)
        if any(self._move_rows):
            encoded.extend(_MOVE_MARKER)
            for row in self._move_rows:
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
        if position == len(data):
            return tile
        if data[position:position + len(_MOVE_MARKER)] != _MOVE_MARKER:
            raise TileEvidenceError('unknown tile revision marker')
        position += len(_MOVE_MARKER)
        for y in range(TILE_EDGE):
            if position + _ROW_LENGTH.size > len(data):
                raise TileEvidenceError('incomplete tile move row length')
            size, = _ROW_LENGTH.unpack_from(data, position)
            position += _ROW_LENGTH.size
            if size > TILE_EDGE * _MOVE_RUN.size or size % _MOVE_RUN.size or position + size > len(data):
                raise TileEvidenceError('invalid move runs in tile row')
            row = data[position:position + size]
            for run in _MOVE_RUN.iter_unpack(row):
                if run[2] not in (_MOVE_KIND_CLEAR, _MOVE_KIND_SAMPLE):
                    raise TileEvidenceError('unknown move evidence kind')
            tile._move_rows[y] = row
            tile._move_bytes += size
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
