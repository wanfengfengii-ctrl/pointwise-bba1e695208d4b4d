"""Deterministic device inputs for the rectangle-move handover examples."""
import json
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from archive import ArchiveDatabase, resume
from inspection import crop
from instrument_demo import LARGE_HEIGHT, LARGE_WIDTH, report_data
from intake import DeviceBlock, DeviceFrame, FrameHeader, capture
from registration import NamedRegions, RecordingIdentity, RecordingSpec, Rect, ScreenSpec, register


SMALL_ID = "move-fixture"
SMALL_SCREEN = Rect(0, 0, 4, 1)


def small_spec():
    return RecordingSpec(
        RecordingIdentity(SMALL_ID, "ink-move"),
        ScreenSpec(4, 1, 10),
        NamedRegions({"row": SMALL_SCREEN}),
    )


def _block(rect, pixels):
    return DeviceBlock(rect, BytesIO(bytes(pixels)))


def _base_commands():
    yield _block(SMALL_SCREEN, (1, 2, 3, 4))


def _shift_commands(make_move):
    yield _block(Rect(0, 0, 1, 1), (9,))
    yield make_move(Rect(0, 0, 3, 1), Rect(1, 0, 4, 1))


def _followup_commands(make_move):
    yield make_move(Rect(1, 0, 2, 1), Rect(2, 0, 3, 1))
    yield _block(Rect(1, 0, 2, 1), (9,))


def small_frames(make_move):
    yield DeviceFrame(FrameHeader(10), _base_commands())
    yield DeviceFrame(FrameHeader(11), _shift_commands(make_move))
    yield DeviceFrame(FrameHeader(12), _followup_commands(make_move))


def large_move_frame(make_move):
    def commands():
        yield make_move(
            Rect(0, 0, LARGE_WIDTH - 1, LARGE_HEIGHT - 1),
            Rect(1, 1, LARGE_WIDTH, LARGE_HEIGHT),
        )

    return DeviceFrame(FrameHeader(12), commands())


def main():
    with TemporaryDirectory(prefix="instrument-move-fixture-") as folder:
        path = Path(folder) / "archive.sqlite"
        with ArchiveDatabase(path) as db:
            register(db, small_spec())
            receipt = capture(db, SMALL_ID, DeviceFrame(FrameHeader(10), _base_commands()))
        with ArchiveDatabase(path) as db:
            data = report_data(crop(db, SMALL_ID, 10, SMALL_SCREEN))
            next_frame = resume(db, SMALL_ID).next_frame
        expected = {
            "pixels": [1, 2, 3, 4],
            "origins": [[10, 0, x, 0] for x in range(4)],
            "gray_total": 10,
        }
        if receipt.frame_no != 10 or next_frame != 11 or data != expected:
            raise AssertionError(f"unexpected V1 fixture: {receipt}, {next_frame}, {data}")
        print(json.dumps({"fixture": "move-base", "next_frame": next_frame, **data}))


if __name__ == "__main__":
    main()
