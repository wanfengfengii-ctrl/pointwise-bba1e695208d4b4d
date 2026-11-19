from __future__ import annotations

from itertools import islice

from .screen import BAND_TILES, ScreenDraft
from .tiles import tile_intersections


def apply_move(
    draft: ScreenDraft,
    frame_no: int,
    command_index: int,
    source: Rect,
    target: Rect,
) -> None:
    """Copy one in-screen rectangle onto another within the current candidate.

    Tiles are visited in topological order away from the source rectangle:
    when the regions overlap, a tile is only overwritten after every tile that
    may still need to sample its pre-command pixels. Tiles are processed in
    cache-sized bands, each tile loaded and spilled once per band, so working
    memory stays independent of the move area. Within a tile, rows walk away
    from the source band and every source span is read before its target span
    is written.
    """
    tiles = list(tile_intersections(target))
    # Walk tiles away from the source region on every axis that overlaps, so
    # a tile is never overwritten before another tile still has to sample it.
    horizontal_overlap = target.right > source.left and target.left < source.right
    rightward = target.left > source.left or (target.left == source.left and horizontal_overlap)
    vertical_overlap = target.bottom > source.top and target.top < source.bottom
    downward = target.top > source.top or (target.top == source.top and vertical_overlap)
    tiles.sort(key=lambda item: (
        item[0].y * (-1 if downward else 1),
        item[0].x * (-1 if rightward else 1),
    ))
    reverse_rows = downward
    ordered = iter(tiles)
    while batch := list(islice(ordered, BAND_TILES)):
        draft.apply_move_tiles(
            batch, source, target, frame_no, command_index, reverse_rows
        )
