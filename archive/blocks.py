import sqlite3

from reconstruction.tiles import Tile, TileKey

from .catalog import PublishedFrame


class MissingTileRevision(LookupError):
    pass


def initialize_blocks(connection: sqlite3.Connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS tile_revisions (
            recording_id TEXT NOT NULL,
            tile_y INTEGER NOT NULL,
            tile_x INTEGER NOT NULL,
            frame_no INTEGER NOT NULL,
            body BLOB NOT NULL,
            PRIMARY KEY (recording_id, tile_y, tile_x, frame_no),
            FOREIGN KEY (recording_id, frame_no) REFERENCES published_frames(recording_id, frame_no)
        ) WITHOUT ROWID
    ''')


def write_tile_revision(connection: sqlite3.Connection, frame: PublishedFrame, key: TileKey, body: bytes) -> None:
    connection.execute('''
        INSERT INTO tile_revisions (recording_id, tile_y, tile_x, frame_no, body)
        VALUES (?, ?, ?, ?, ?)
    ''', (frame.recording_id, key.y, key.x, frame.frame_no, body))


def read_tile_at(connection: sqlite3.Connection, recording_id: str, frame_no: int, key: TileKey) -> Tile:
    row = connection.execute('''
        SELECT body FROM tile_revisions
        WHERE recording_id = ? AND tile_y = ? AND tile_x = ? AND frame_no <= ?
        ORDER BY frame_no DESC LIMIT 1
    ''', (recording_id, key.y, key.x, frame_no)).fetchone()
    if row is None:
        raise MissingTileRevision((recording_id, frame_no, key))
    return Tile.from_bytes(row[0])
