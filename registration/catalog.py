from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from .geometry import Rect
from .identity import RecordingIdentity
from .regions import NamedRegions
from .specification import RecordingSpec, ScreenSpec

if TYPE_CHECKING:
    from archive.database import ArchiveDatabase


class RegistrationConflict(ValueError):
    pass


class UnknownRecording(LookupError):
    pass


def initialize_registrations(connection: sqlite3.Connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS recordings (
            recording_id TEXT PRIMARY KEY NOT NULL,
            device_id TEXT NOT NULL,
            width INTEGER NOT NULL CHECK (width > 0),
            height INTEGER NOT NULL CHECK (height > 0),
            first_frame INTEGER NOT NULL
        )
    ''')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS named_regions (
            recording_id TEXT NOT NULL REFERENCES recordings(recording_id),
            name TEXT NOT NULL,
            x0 INTEGER NOT NULL,
            y0 INTEGER NOT NULL,
            x1 INTEGER NOT NULL,
            y1 INTEGER NOT NULL,
            PRIMARY KEY (recording_id, name)
        ) WITHOUT ROWID
    ''')


def _find_recording(connection: sqlite3.Connection, recording_id: str) -> RecordingSpec | None:
    if not isinstance(recording_id, str):
        return None
    row = connection.execute('''
        SELECT device_id, width, height, first_frame
        FROM recordings WHERE recording_id = ?
    ''', (recording_id,)).fetchone()
    if row is None:
        return None
    device_id, width, height, first_frame = row
    areas = connection.execute('''
        SELECT name, x0, y0, x1, y1 FROM named_regions WHERE recording_id = ?
    ''', (recording_id,))
    regions = NamedRegions({name: Rect(x0, y0, x1, y1) for name, x0, y0, x1, y1 in areas})
    return RecordingSpec(RecordingIdentity(recording_id, device_id), ScreenSpec(width, height, first_frame), regions)


def require_recording(connection: sqlite3.Connection, recording_id: str) -> RecordingSpec:
    spec = _find_recording(connection, recording_id)
    if spec is None:
        raise UnknownRecording(recording_id)
    return spec


def register(db: ArchiveDatabase, spec: RecordingSpec) -> RecordingIdentity:
    recording_id = spec.identity.recording_id
    with db.transaction() as connection:
        original = _find_recording(connection, recording_id)
        if original is not None:
            if original != spec:
                raise RegistrationConflict(f'conflicting registration for {recording_id!r}')
            return original.identity
        screen = spec.screen
        connection.execute('''
            INSERT INTO recordings (recording_id, device_id, width, height, first_frame)
            VALUES (?, ?, ?, ?, ?)
        ''', (recording_id, spec.identity.device_id, screen.width, screen.height, screen.first_frame))
        connection.executemany('''
            INSERT INTO named_regions (recording_id, name, x0, y0, x1, y1)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            (recording_id, name, rect.left, rect.top, rect.right, rect.bottom)
            for name, rect in spec.regions.entries
        ))
    return spec.identity
