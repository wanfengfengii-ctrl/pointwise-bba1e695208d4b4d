from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from archive import ArchiveDatabase
from intake import DeviceBlock, DeviceFrame, FrameHeader, capture
from registration import NamedRegions, RecordingIdentity, RecordingSpec, Rect, ScreenSpec, register


RECORDING = 'chamber-a'
SCREEN = Rect(0, 0, 2, 2)
LEFT = Rect(0, 0, 1, 2)
RIGHT = Rect(1, 0, 2, 2)
BASE = (37, 2, 3, 4)
BASE_ORIGINS = ((10, 0, 0, 0), (10, 0, 1, 0), (10, 0, 0, 1), (10, 0, 1, 1))


def spec() -> RecordingSpec:
    return RecordingSpec(
        RecordingIdentity(RECORDING, 'ink-01'), ScreenSpec(2, 2, 10),
        NamedRegions({'reading': LEFT, 'alarm': RIGHT}),
    )


def block(rect, pixels) -> DeviceBlock:
    return DeviceBlock(rect, BytesIO(bytes(pixels)))


def send(db, frame_no, *blocks, before_commit=None):
    frame = DeviceFrame(FrameHeader(frame_no), iter(blocks))
    return capture(db, RECORDING, frame, before_commit=before_commit)


def seed(db, pixels=BASE):
    register(db, spec())
    return send(db, 10, block(SCREEN, pixels))


class RecordingCase(TestCase):
    def setUp(self):
        folder = TemporaryDirectory(prefix='instrument-test-')
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'archive.sqlite'

    def open_archive(self):
        return ArchiveDatabase(self.path)

    def assert_report(self, report, pixels, origins, gray_total):
        self.assertEqual(report.pixels, bytes(pixels))
        self.assertEqual(
            tuple((origin.frame_no, origin.block_index, origin.x, origin.y) for origin in report.origins),
            tuple(origins),
        )
        self.assertEqual(report.gray_total, gray_total)
        self.assertIs(type(report.gray_total), int)
