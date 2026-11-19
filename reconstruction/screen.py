from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator

from registration.geometry import Rect
from registration.specification import ScreenSpec

from .origins import Origin
from .staging import TileStaging
from .tiles import TILE_EDGE, Tile, TileKey, tile_intersections


# A move band keeps this many tiles resident. Loaded tiles run roughly half a
# MiB each once grays, origins and move evidence are encoded.
BAND_TILES = 22
# Frames containing moves may touch the whole screen; give their disk-backed
# staging a larger resident budget so full bands never thrash the workspace.
MOVE_STAGING_BYTES = 8 * 1024 * 1024
# Rolling cache for source tiles that sit outside the current target band.
# A horizontal move only needs the immediate neighbour column; keeping a few
# decoded tiles means each archive tile is read once instead of once per row.
SOURCE_CACHE_TILES = 4


class ScreenDraft:
    def __init__(
        self,
        screen: ScreenSpec,
        load_base: Callable[[TileKey], Tile] | None,
    ) -> None:
        self._screen = screen
        self._load_base = load_base
        self._staging = TileStaging()
        self._source_cache: OrderedDict[TileKey, Tile] = OrderedDict()

    def _tile(self, key: TileKey) -> Tile:
        tile = self._staging.read(key)
        if tile is None:
            cached = self._source_cache.get(key)
            if cached is not None:
                self._source_cache.move_to_end(key)
                return cached
            if self._load_base is None:
                raise RuntimeError('draft read without a published base')
            tile = self._load_base(key)
            self._source_cache[key] = tile
            while len(self._source_cache) > SOURCE_CACHE_TILES:
                self._source_cache.popitem(last=False)
        return tile

    def _source_tile(
        self,
        key: TileKey,
        band: OrderedDict[TileKey, tuple[Tile, Rect]],
        *,
        skip_band: bool = False,
    ) -> Tile:
        """Pristine or topology-ordered source content for a read-only sample.

        Tiles still queued in the current band have not been overwritten yet,
        so the live band tile is valid. Tiles already handed to staging (or the
        active tile, which may be partly overwritten) are read through the
        rolling pristine cache, never updated by candidate writes.
        """
        entry = None if skip_band else band.get(key)
        if entry is not None:
            return entry[0]
        cached = self._source_cache.get(key)
        if cached is not None:
            self._source_cache.move_to_end(key)
            return cached
        if self._load_base is None:
            raise RuntimeError('draft read without a published base')
        tile = self._load_base(key)
        self._source_cache[key] = tile
        while len(self._source_cache) > SOURCE_CACHE_TILES:
            self._source_cache.popitem(last=False)
        return tile

    def _store(self, key: TileKey, tile: Tile) -> None:
        # A resident source snapshot is stale once this tile becomes a candidate.
        self._source_cache.pop(key, None)
        self._staging.write(key, tile)

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
            self._store(key, tile)
            position += count

    def apply_move_tiles(
        self,
        items: Iterable[tuple[TileKey, Rect]],
        source: Rect,
        target: Rect,
        frame_no: int,
        command_index: int,
        reverse_rows: bool,
    ) -> None:
        """Apply one move over a cache-sized band of tiles.

        Every tile in the band is loaded once and handed back to staging once;
        source reads inside the band use the live tile so overlapping target
        and source spans never observe a stale on-disk revision. The band cap
        keeps move working memory independent of the rectangle area.
        """
        band: OrderedDict[TileKey, tuple[Tile, Rect]] = OrderedDict()
        self._staging.raise_cache_limit(MOVE_STAGING_BYTES)
        for key, clip in items:
            resident = self._staging.resident(key)
            if resident is None:
                resident = self._tile(key)
            band[key] = (resident, clip)
            if len(band) >= BAND_TILES:
                self._process_move_band(
                    band, source, target, frame_no, command_index, reverse_rows
                )
                band.clear()
        if band:
            self._process_move_band(
                band, source, target, frame_no, command_index, reverse_rows
            )

    def _read_row_runs(
        self,
        x: int,
        y: int,
        count: int,
        active_key: TileKey,
        band: OrderedDict[TileKey, tuple[Tile, Rect]],
    ) -> tuple[bytes, list[tuple]]:
        """Read a row interval as pixels and window-relative origin runs."""
        pixels = bytearray()
        runs: list[tuple] = []
        row = Rect(x, y, x + count, y + 1)
        for key, span in tile_intersections(row):
            entry = band.get(key)
            if entry is not None and key != active_key:
                # A not-yet-processed band tile still holds pre-write content.
                tile = entry[0]
            else:
                # The active tile may already be partly overwritten on this
                # row, so source reads come from the pristine rolling cache.
                tile = self._source_tile(key, band, skip_band=(key == active_key))
            local_y = y % TILE_EDGE
            local_x = span.left - key.bounds.left
            if local_x == 0 and span.width == TILE_EDGE:
                chunk, chunk_runs = tile.read_row_runs(local_y)
            else:
                chunk, chunk_runs = tile.read_clip_runs(local_x, local_y, span.width)
            offset = len(pixels)
            for start, size, frame, block, source_x, source_y in chunk_runs:
                runs.append((start + offset, size, frame, block, source_x, source_y))
            pixels.extend(chunk)
        return bytes(pixels), runs

    def _process_move_band(self, band, source, target, frame_no, command_index, reverse_rows) -> None:
        shift_x = target.left - source.left
        shift_y = target.top - source.top
        for key, (tile, clip) in band.items():
            local_dst = Rect(
                clip.left - key.bounds.left,
                clip.top - key.bounds.top,
                clip.right - key.bounds.left,
                clip.bottom - key.bounds.top,
            )
            local_src = Rect(
                local_dst.left - shift_x,
                local_dst.top - shift_y,
                local_dst.right - shift_x,
                local_dst.bottom - shift_y,
            )
            inside = (
                0 <= local_src.left and local_src.right <= TILE_EDGE
                and 0 <= local_src.top and local_src.bottom <= TILE_EDGE
            )
            if inside:
                # Source cells are all local: a directional in-tile run shift.
                tile.shift_rect_in_tile(local_src, local_dst, frame_no, command_index)
            else:
                # Edge tile whose source reaches into a neighbouring tile.
                self._shift_cross_tile(
                    tile, key, clip, source, target, band, frame_no,
                    command_index, reverse_rows,
                )
        for key, (tile, _clip) in band.items():
            self._store(key, tile)

    def _shift_cross_tile(self, tile, key, clip, source, target, band, frame_no,
                          command_index, reverse_rows) -> None:
        """Move an edge clip whose source spans another tile.

        Each source row is fully read across its tiles before being written to
        the destination row; across a tile boundary those two row intervals do
        not alias, and rows are walked away from the source band so vertical
        overlap stays on the pre-write snapshot.
        """
        shift_x = target.left - source.left
        shift_y = target.top - source.top
        local_x = clip.left - key.bounds.left
        row_numbers = range(clip.top, clip.bottom)
        if reverse_rows:
            row_numbers = range(clip.bottom - 1, clip.top - 1, -1)
        for dst_y in row_numbers:
            src_y = dst_y - shift_y
            src_left = clip.left - shift_x
            pixels, runs = self._read_row_runs(
                src_left, src_y, clip.width, key, band
            )
            # Window runs start at column 0; re-base onto the tile-local dst.
            local_runs = [(start + local_x, size, frame, block, source_x, source_y)
                          for start, size, frame, block, source_x, source_y in runs]
            tile.write_move_clip_runs(
                local_x,
                dst_y - key.bounds.top,
                pixels,
                local_runs,
                frame_no,
                command_index,
                src_left,
                src_y,
            )

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
