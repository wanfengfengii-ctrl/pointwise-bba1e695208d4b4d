from collections.abc import Sequence

from reconstruction.origins import Origin


class IncompleteEvidence(ValueError):
    pass


class EvidenceBuilder:
    def __init__(self, expected_count: int) -> None:
        self._expected_count = expected_count
        self._origins: list[Origin] = []

    def append_span(self, pixels: bytes, origins: Sequence[Origin]) -> None:
        if len(pixels) != len(origins):
            raise IncompleteEvidence('gray values and origins must have the same length')
        if len(self._origins) + len(origins) > self._expected_count:
            raise IncompleteEvidence('report contains more evidence than the requested area')
        self._origins.extend(origins)

    def finish(self) -> tuple[Origin, ...]:
        if len(self._origins) != self._expected_count:
            raise IncompleteEvidence('report is missing pixel origins')
        return tuple(self._origins)
