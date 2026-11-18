from registration.specification import is_storage_integer

from .frames import FrameHeader


class FrameSequenceError(ValueError):
    pass


def require_expected_frame(header: FrameHeader, expected: int) -> None:
    if not is_storage_integer(header.frame_no) or header.frame_no != expected:
        raise FrameSequenceError(f'expected frame {expected}, got {header.frame_no!r}')
