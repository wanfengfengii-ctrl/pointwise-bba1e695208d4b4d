from collections.abc import Iterator

from .frames import BlockBody, DeviceBlock


SPAN_LIMIT = 8192


class PayloadError(ValueError):
    pass


def _checked_read(body: BlockBody, limit: int) -> bytes:
    chunk = body.read(limit)
    if not isinstance(chunk, bytes) or len(chunk) > limit:
        raise PayloadError('block reader must return bytes within the requested size')
    return chunk


def iter_payload_spans(block: DeviceBlock) -> Iterator[tuple[int, int, bytes]]:
    width = block.rect.width
    received = 0
    for y in range(block.rect.height):
        for x in range(0, width, SPAN_LIMIT):
            length = min(SPAN_LIMIT, width - x)
            chunk = _checked_read(block.body, length)
            if len(chunk) < length:
                buffer = bytearray(chunk)
                while len(buffer) < length:
                    more = _checked_read(block.body, length - len(buffer))
                    if not more:
                        total = received + len(buffer)
                        raise PayloadError(f'truncated block after {total} of {block.rect.area} bytes')
                    buffer.extend(more)
                chunk = bytes(buffer)
            received += length
            yield x, y, chunk
    if _checked_read(block.body, 1):
        raise PayloadError(f'block contains extra bytes after {received} bytes')
