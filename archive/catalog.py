import sqlite3
from dataclasses import dataclass

from registration.specification import is_storage_integer


class FrameNotPublished(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class PublishedFrame:
    recording_id: str
    frame_no: int


def initialize_frames(connection: sqlite3.Connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS published_frames (
            recording_id TEXT NOT NULL REFERENCES recordings(recording_id),
            frame_no INTEGER NOT NULL,
            PRIMARY KEY (recording_id, frame_no)
        ) WITHOUT ROWID
    ''')


def insert_frame(connection: sqlite3.Connection, frame: PublishedFrame) -> None:
    connection.execute('''
        INSERT INTO published_frames (recording_id, frame_no) VALUES (?, ?)
    ''', (frame.recording_id, frame.frame_no))


def require_published_frame(connection: sqlite3.Connection, recording_id: str, frame_no: int) -> PublishedFrame:
    if not is_storage_integer(frame_no):
        raise FrameNotPublished((recording_id, frame_no))
    row = connection.execute('''
        SELECT frame_no FROM published_frames WHERE recording_id = ? AND frame_no = ?
    ''', (recording_id, frame_no)).fetchone()
    if row is None:
        raise FrameNotPublished((recording_id, frame_no))
    return PublishedFrame(recording_id, row[0])


def last_published_frame(connection: sqlite3.Connection, recording_id: str) -> PublishedFrame | None:
    row = connection.execute('''
        SELECT frame_no FROM published_frames
        WHERE recording_id = ? ORDER BY frame_no DESC LIMIT 1
    ''', (recording_id,)).fetchone()
    return None if row is None else PublishedFrame(recording_id, row[0])
