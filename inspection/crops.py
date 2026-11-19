from collections.abc import Iterator
from functools import lru_cache

from archive.blocks import read_tile_at
from archive.catalog import PublishedFrame, require_published_frame
from archive.database import ArchiveDatabase
from reconstruction.origins import MoveEvidence, Origin
from reconstruction.tiles import TILE_EDGE, Tile, TileKey, tile_intersections
from registration.catalog import require_recording
from registration.geometry import Rect, RectangleError
from registration.specification import RecordingSpec

from .reports import MoveRegionReport, RegionReport, build_move_report, build_region_report


def _region_spans(db: ArchiveDatabase, frame: PublishedFrame, rect: Rect) -> Iterator[tuple[bytes, tuple[Origin, ...]]]:
    @lru_cache(maxsize=4)
    def load(key: TileKey) -> Tile:
        return read_tile_at(db.connection, frame.recording_id, frame.frame_no, key)

    try:
        for y in range(rect.top, rect.bottom):
            row = Rect(rect.left, y, rect.right, y + 1)
            for key, span in tile_intersections(row):
                tile = load(key)
                yield tile.read_span(span.left % TILE_EDGE, y % TILE_EDGE, span.width)
    finally:
        load.cache_clear()


def _move_spans(
    db: ArchiveDatabase,
    frame: PublishedFrame,
    rect: Rect,
) -> Iterator[tuple[bytes, tuple[Origin, ...], tuple[MoveEvidence | None, ...]]]:
    @lru_cache(maxsize=4)
    def load(key: TileKey) -> Tile:
        return read_tile_at(db.connection, frame.recording_id, frame.frame_no, key)

    try:
        for y in range(rect.top, rect.bottom):
            row = Rect(rect.left, y, rect.right, y + 1)
            for key, span in tile_intersections(row):
                tile = load(key)
                local_x = span.left % TILE_EDGE
                local_y = y % TILE_EDGE
                pixels, origins = tile.read_span(local_x, local_y, span.width)
                moves = tile.read_move_span(local_x, local_y, span.width)
                yield pixels, origins, moves
    finally:
        load.cache_clear()


def _validate_rect(recording: RecordingSpec, rect: Rect) -> None:
    if not isinstance(rect, Rect):
        raise RectangleError('crop requires a Rect')
    rect.require_inside(recording.screen.bounds)


def _report(db: ArchiveDatabase, recording: RecordingSpec, frame_no: int, rect: Rect) -> RegionReport:
    recording_id = recording.identity.recording_id
    frame = require_published_frame(db.connection, recording_id, frame_no)
    _validate_rect(recording, rect)
    return build_region_report(recording_id, frame.frame_no, rect, _region_spans(db, frame, rect))


def _move_report(db: ArchiveDatabase, recording: RecordingSpec, frame_no: int, rect: Rect) -> MoveRegionReport:
    recording_id = recording.identity.recording_id
    frame = require_published_frame(db.connection, recording_id, frame_no)
    _validate_rect(recording, rect)
    return build_move_report(recording_id, frame.frame_no, rect, _move_spans(db, frame, rect))


def crop(db: ArchiveDatabase, recording_id: str, frame_no: int, rect: Rect) -> RegionReport:
    recording = require_recording(db.connection, recording_id)
    return _report(db, recording, frame_no, rect)


def crop_with_moves(db: ArchiveDatabase, recording_id: str, frame_no: int, rect: Rect) -> MoveRegionReport:
    recording = require_recording(db.connection, recording_id)
    return _move_report(db, recording, frame_no, rect)


def inspect(db: ArchiveDatabase, recording_id: str, frame_no: int, name: str) -> RegionReport:
    recording = require_recording(db.connection, recording_id)
    rect = recording.regions.resolve(name)
    return _report(db, recording, frame_no, rect)
