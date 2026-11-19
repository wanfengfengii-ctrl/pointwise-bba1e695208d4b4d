from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from reconstruction.origins import MoveEvidence, Origin
from registration.geometry import Rect

from .evidence import EvidenceBuilder
from .totals import GrayTotal


@dataclass(frozen=True, slots=True)
class RegionReport:
    recording_id: str
    frame_no: int
    rect: Rect
    pixels: bytes
    origins: tuple[Origin, ...]
    gray_total: int


@dataclass(frozen=True, slots=True)
class MoveRegionReport:
    recording_id: str
    frame_no: int
    rect: Rect
    pixels: bytes
    origins: tuple[Origin, ...]
    gray_total: int
    moves: tuple[MoveEvidence | None, ...]


def build_region_report(
    recording_id: str,
    frame_no: int,
    rect: Rect,
    spans: Iterable[tuple[bytes, Sequence[Origin]]],
) -> RegionReport:
    evidence = EvidenceBuilder(rect.area)
    pixels = bytearray()
    total = GrayTotal()
    for span, origins in spans:
        evidence.append_span(span, origins)
        pixels.extend(span)
        total.add_span(span)
    return RegionReport(recording_id, frame_no, rect, bytes(pixels), evidence.finish(), total.value)


def build_move_report(
    recording_id: str,
    frame_no: int,
    rect: Rect,
    spans: Iterable[tuple[bytes, Sequence[Origin], Sequence[MoveEvidence | None]]],
) -> MoveRegionReport:
    evidence = EvidenceBuilder(rect.area)
    pixels = bytearray()
    moves: list[MoveEvidence | None] = []
    total = GrayTotal()
    for span, origins, span_moves in spans:
        evidence.append_span(span, origins)
        if len(span_moves) != len(span):
            raise IncompleteMoveEvidence('move evidence must match the requested area pixel by pixel')
        if len(moves) + len(span_moves) > rect.area:
            raise IncompleteMoveEvidence('report contains more move evidence than the requested area')
        pixels.extend(span)
        moves.extend(span_moves)
        total.add_span(span)
    if len(moves) != rect.area:
        raise IncompleteMoveEvidence('report is missing pixel move evidence')
    return MoveRegionReport(
        recording_id, frame_no, rect, bytes(pixels), evidence.finish(), total.value, tuple(moves)
    )


class IncompleteMoveEvidence(ValueError):
    pass
