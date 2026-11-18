from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from registration.catalog import require_recording

from .catalog import last_published_frame

if TYPE_CHECKING:
    from .database import ArchiveDatabase


@dataclass(frozen=True, slots=True)
class ResumeState:
    last_frame: int | None
    next_frame: int
    base_frame: int | None


def resume(db: ArchiveDatabase, recording_id: str) -> ResumeState:
    recording = require_recording(db.connection, recording_id)
    confirmed = last_published_frame(db.connection, recording_id)
    if confirmed is None:
        return ResumeState(None, recording.screen.first_frame, None)
    return ResumeState(confirmed.frame_no, confirmed.frame_no + 1, confirmed.frame_no)
