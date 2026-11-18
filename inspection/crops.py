from collections.abc import Iterator
from functools import lru_cache

from archive.blocks import read_tile_at
from archive.catalog import PublishedFrame, require_published_frame
from archive.database import ArchiveDatabase
from reconstruction.origins import Origin
from reconstruction.tiles import TILE_EDGE, Tile, TileKey, tile_intersections
from registration.catalog import require_recording
from registration.geometry import Rect, RectangleError
from registration.specification import RecordingSpec

from .reports import RegionReport, build_region_report


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


def _report(db: ArchiveDatabase, recording: RecordingSpec, frame_no: int, rect: Rect) -> RegionReport:
    recording_id = recording.identity.recording_id
    frame = require_published_frame(db.connection, recording_id, frame_no)
    if not isinstance(rect, Rect):
        raise RectangleError('crop requires a Rect')
    rect.require_inside(recording.screen.bounds)
    return build_region_report(recording_id, frame.frame_no, rect, _region_spans(db, frame, rect))


def crop(db: ArchiveDatabase, recording_id: str, frame_no: int, rect: Rect) -> RegionReport:
    recording = require_recording(db.connection, recording_id)
    return _report(db, recording, frame_no, rect)


def inspect(db: ArchiveDatabase, recording_id: str, frame_no: int, name: str) -> RegionReport:
    recording = require_recording(db.connection, recording_id)
    rect = recording.regions.resolve(name)
    return _report(db, recording, frame_no, rect)
