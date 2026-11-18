from collections.abc import Callable, Iterable
from contextlib import closing

from reconstruction.tiles import TileKey
from registration.identity import RecordingIdentity

from .blocks import write_tile_revision
from .catalog import PublishedFrame, insert_frame
from .database import ArchiveDatabase
from .resume import resume


class PublicationConflict(ValueError):
    pass


def publish_frame(
    db: ArchiveDatabase,
    recording: RecordingIdentity,
    frame_no: int,
    tiles: Iterable[tuple[TileKey, bytes]],
    *,
    before_commit: Callable[[], None] | None = None,
) -> PublishedFrame:
    frame = PublishedFrame(recording.recording_id, frame_no)
    with db.transaction(before_commit=before_commit) as connection:
        state = resume(db, frame.recording_id)
        if frame_no != state.next_frame:
            raise PublicationConflict(f'expected publication {state.next_frame}, got {frame_no}')
        insert_frame(connection, frame)
        # Close a streaming candidate promptly if an archive write fails partway through it.
        iterator = iter(tiles)
        close_iterator = getattr(iterator, 'close', None)
        if close_iterator is None:
            for key, body in iterator:
                write_tile_revision(connection, frame, key, body)
        else:
            with closing(iterator):
                for key, body in iterator:
                    write_tile_revision(connection, frame, key, body)
    return frame
