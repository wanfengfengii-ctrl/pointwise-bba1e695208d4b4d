from __future__ import annotations

from collections.abc import Iterable

from .frames import DeviceFrame


class DeviceStream:
    def __init__(self, frames: Iterable[DeviceFrame]) -> None:
        self._source = iter(frames)
        self._closed = False

    def next_frame(self) -> DeviceFrame | None:
        if self._closed:
            return None
        try:
            return next(self._source)
        except StopIteration:
            self.close()
            return None

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            close_source = getattr(self._source, 'close', None)
            if close_source is not None:
                close_source()

    def __enter__(self) -> DeviceStream:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
