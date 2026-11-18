class GrayTotal:
    def __init__(self) -> None:
        self._value = 0

    def add_span(self, pixels: bytes) -> None:
        self._value += sum(pixels)

    @property
    def value(self) -> int:
        return self._value
