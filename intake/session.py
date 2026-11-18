from collections.abc import Callable

from archive.blocks import read_tile_at
from archive.catalog import PublishedFrame
from archive.database import ArchiveDatabase
from archive.publish import publish_frame
from archive.resume import resume
from reconstruction.patches import apply_patch
from reconstruction.screen import ScreenDraft
from reconstruction.tiles import Tile, TileKey
from registration.catalog import require_recording

from .frames import BaseFrameError, DeviceFrame, validate_base_block, validate_block_rectangle
from .payloads import iter_payload_spans
from .sequence import require_expected_frame


def capture(
    db: ArchiveDatabase,
    recording_id: str,
    frame: DeviceFrame,
    *,
    before_commit: Callable[[], None] | None = None,
) -> PublishedFrame:
    recording = require_recording(db.connection, recording_id)
    state = resume(db, recording_id)
    require_expected_frame(frame.header, state.next_frame)
    frame.header.require_permanent()
    frame_no = frame.header.frame_no
    base_frame = state.base_frame
    is_base = base_frame is None

    def load_base(key: TileKey) -> Tile:
        if base_frame is None:
            raise RuntimeError('base loader called without a published base')
        return read_tile_at(db.connection, recording_id, base_frame, key)

    with ScreenDraft(recording.screen, None if is_base else load_base) as draft:
        has_blocks = False
        for block_index, block in enumerate(frame.blocks):
            validate_block_rectangle(block, recording.screen)
            if is_base:
                validate_base_block(block, recording.screen, block_index)
            apply_patch(draft, frame_no, block_index, block.rect, iter_payload_spans(block))
            has_blocks = True
        if is_base and not has_blocks:
            raise BaseFrameError('first frame is missing its full-screen block')
        return publish_frame(db, recording.identity, frame_no, draft.iter_dirty_tiles(), before_commit=before_commit)
