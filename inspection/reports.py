from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from reconstruction.origins import Origin
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
