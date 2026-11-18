from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from os import PathLike

from registration.catalog import initialize_registrations

from .blocks import initialize_blocks
from .catalog import initialize_frames


class ArchiveDatabase:
    def __init__(self, path: str | PathLike[str]) -> None:
        self.connection = sqlite3.connect(path, isolation_level=None)
        try:
            self.connection.execute('PRAGMA foreign_keys = ON')
            self.connection.execute('PRAGMA cache_size = -768')
            self.connection.execute('PRAGMA mmap_size = 0')
            self.connection.execute('PRAGMA temp_store = FILE')
            self.initialize()
        except BaseException:
            self.connection.close()
            raise

    def initialize(self) -> None:
        with self.transaction() as connection:
            initialize_registrations(connection)
            initialize_frames(connection)
            initialize_blocks(connection)

    @contextmanager
    def transaction(self, *, before_commit: Callable[[], None] | None = None) -> Iterator[sqlite3.Connection]:
        self.connection.execute('BEGIN IMMEDIATE')
        try:
            yield self.connection
            if before_commit is not None:
                before_commit()
            self.connection.commit()
        except BaseException:
            if self.connection.in_transaction:
                self.connection.rollback()
            raise

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> ArchiveDatabase:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
