import sqlite3
from collections import OrderedDict
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

from .tiles import Tile, TileKey


_CACHE_BYTES = 4 * 1024 * 1024


class TileStaging:
    def __init__(self) -> None:
        self._directory = TemporaryDirectory(prefix='instrument-stage-')
        try:
            self._db = sqlite3.connect(Path(self._directory.name) / 'draft.sqlite')
            try:
                self._db.execute('PRAGMA journal_mode = OFF')
                self._db.execute('PRAGMA cache_size = -512')
                self._db.execute('PRAGMA mmap_size = 0')
                self._db.execute('''
                    CREATE TABLE candidate (
                        tile_y INTEGER NOT NULL,
                        tile_x INTEGER NOT NULL,
                        body BLOB NOT NULL,
                        PRIMARY KEY (tile_y, tile_x)
                    ) WITHOUT ROWID
                ''')
            except BaseException:
                self._db.close()
                raise
        except BaseException:
            self._directory.cleanup()
            raise
        self._cache: OrderedDict[TileKey, tuple[Tile, int]] = OrderedDict()
        self._resident = 0
        self._closed = False

    def read(self, key: TileKey) -> Tile | None:
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached[0]
        row = self._db.execute('''
            SELECT body FROM candidate WHERE tile_y = ? AND tile_x = ?
        ''', (key.y, key.x)).fetchone()
        return None if row is None else Tile.from_bytes(row[0])

    def _store(self, key: TileKey, tile: Tile) -> None:
        self._db.execute('''
            INSERT INTO candidate (tile_y, tile_x, body) VALUES (?, ?, ?)
            ON CONFLICT (tile_y, tile_x) DO UPDATE SET body = excluded.body
        ''', (key.y, key.x, tile.to_bytes()))

    def _spill_oldest(self) -> None:
        key, (tile, charge) = self._cache.popitem(last=False)
        self._resident -= charge
        self._store(key, tile)

    def write(self, key: TileKey, tile: Tile) -> None:
        previous = self._cache.pop(key, None)
        if previous is not None:
            self._resident -= previous[1]
        charge = tile.resident_bytes
        self._cache[key] = (tile, charge)
        self._resident += charge
        while self._resident > _CACHE_BYTES:
            self._spill_oldest()

    def iter_changed(self) -> Iterator[tuple[TileKey, bytes]]:
        while self._cache:
            self._spill_oldest()
        rows = self._db.execute('SELECT tile_y, tile_x, body FROM candidate ORDER BY tile_y, tile_x')
        try:
            for tile_y, tile_x, body in rows:
                yield TileKey(tile_x, tile_y), body
        finally:
            rows.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._cache.clear()
        try:
            self._db.close()
        finally:
            self._directory.cleanup()
