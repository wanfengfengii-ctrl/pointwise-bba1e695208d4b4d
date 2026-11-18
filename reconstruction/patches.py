from collections.abc import Iterable

from registration.geometry import Rect

from .origins import origin_at
from .screen import ScreenDraft


def apply_patch(
    draft: ScreenDraft,
    frame_no: int,
    block_index: int,
    rect: Rect,
    spans: Iterable[tuple[int, int, bytes]],
) -> None:
    for block_x, block_y, data in spans:
        x, y = rect.left + block_x, rect.top + block_y
        origin = origin_at(frame_no, block_index, rect, x, y)
        draft.write_span(x, y, data, origin)
