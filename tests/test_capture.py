from archive import FrameNotPublished, PublishedFrame, ResumeState, resume
from inspection import crop
from intake import DeviceBlock, DeviceFrame, FrameHeader, FrameModeError, PayloadError, capture
from reconstruction import TILE_EDGE
from registration import NamedRegions, RecordingIdentity, RecordingSpec, Rect, RectangleError, ScreenSpec, register
from tests import BASE, BASE_ORIGINS, LEFT, RECORDING, RIGHT, SCREEN, RecordingCase, block, seed, send


POINT = Rect(0, 0, 1, 1)


class SplitBody:
    def __init__(self, data, maximum):
        self.data = bytes(data)
        self.maximum = maximum
        self.position = 0
        self.sizes = []

    def read(self, requested):
        count = min(requested, self.maximum, len(self.data) - self.position)
        chunk = self.data[self.position:self.position + count]
        self.position += count
        self.sizes.append(count)
        return chunk


class BrokenBody:
    def __init__(self):
        self.calls = 0

    def read(self, requested):
        self.calls += 1
        if self.calls == 1:
            return bytes((61,))
        raise OSError('device input interrupted')


class CaptureTests(RecordingCase):
    def assert_frame_unchanged(self, db):
        self.assertEqual(resume(db, RECORDING), ResumeState(10, 11, 10))
        with self.assertRaises(FrameNotPublished):
            crop(db, RECORDING, 11, SCREEN)
        self.assert_report(crop(db, RECORDING, 10, SCREEN), BASE, BASE_ORIGINS, 46)

    def test_overlapping_permanent_patches(self):
        with self.open_archive() as db:
            seed(db)
            self.assertEqual(send(db, 11, block(POINT, (82,)), block(POINT, (91,))), PublishedFrame(RECORDING, 11))
            self.assert_report(crop(db, RECORDING, 11, POINT), (91,), ((11, 1, 0, 0),), 91)
            self.assert_report(crop(db, RECORDING, 10, POINT), (37,), ((10, 0, 0, 0),), 37)

    def test_cross_tile_order(self):
        edge = TILE_EDGE
        screen = Rect(0, 0, edge + 2, 1)
        overlap = Rect(edge - 1, 0, edge + 2, 1)
        with self.open_archive() as db:
            register(db, RecordingSpec(
                RecordingIdentity(RECORDING, 'ink-01'), ScreenSpec(edge + 2, 1, 10), NamedRegions({'boundary': overlap}),
            ))
            send(db, 10, block(screen, (37,) * screen.width))
            send(db, 11, block(Rect(edge - 1, 0, edge + 1, 1), (82, 83)), block(Rect(edge, 0, edge + 2, 1), (91, 92)))
            self.assert_report(crop(db, RECORDING, 11, overlap), (82, 91, 92),
                               ((11, 0, 0, 0), (11, 1, 0, 0), (11, 1, 1, 0)), 265)
            self.assert_report(crop(db, RECORDING, 10, overlap), (37, 37, 37),
                               ((10, 0, edge - 1, 0), (10, 0, edge, 0), (10, 0, edge + 1, 0)), 111)

    def test_empty_permanent(self):
        with self.open_archive() as db:
            seed(db)
            self.assertEqual(send(db, 11), PublishedFrame(RECORDING, 11))
            self.assert_report(crop(db, RECORDING, 11, SCREEN), BASE, BASE_ORIGINS, 46)
            self.assertEqual(resume(db, RECORDING), ResumeState(11, 12, 11))
            send(db, 12, block(POINT, (5,)))
            self.assert_report(crop(db, RECORDING, 11, SCREEN), BASE, BASE_ORIGINS, 46)

    def test_untouched_origin(self):
        with self.open_archive() as db:
            seed(db)
            send(db, 11, block(RIGHT, (19, 20)))
            old = crop(db, RECORDING, 10, LEFT)
            latest = crop(db, RECORDING, 11, LEFT)
            self.assert_report(latest, (37, 3), ((10, 0, 0, 0), (10, 0, 0, 1)), 40)
            self.assertEqual((latest.pixels, latest.origins, latest.gray_total), (old.pixels, old.origins, old.gray_total))

    def test_paired_origins(self):
        with self.open_archive() as db:
            seed(db)
            send(db, 11, block(RIGHT, (19, 20)))
            self.assert_report(crop(db, RECORDING, 11, SCREEN), (37, 19, 3, 20),
                               ((10, 0, 0, 0), (11, 0, 0, 0), (10, 0, 0, 1), (11, 0, 0, 1)), 79)

    def test_short_block(self):
        with self.open_archive() as db:
            seed(db)
            with self.assertRaisesRegex(PayloadError, 'truncated'):
                send(db, 11, block(RIGHT, (19,)))
            self.assert_frame_unchanged(db)

    def test_short_after_valid_block(self):
        with self.open_archive() as db:
            seed(db)
            accepted = SplitBody((82,), 1)
            with self.assertRaisesRegex(PayloadError, 'truncated'):
                send(db, 11, DeviceBlock(POINT, accepted), block(RIGHT, (19,)))
            self.assertEqual(accepted.sizes, [1, 0])
            self.assert_frame_unchanged(db)
        with self.open_archive() as db:
            self.assert_frame_unchanged(db)
            send(db, 11, block(RIGHT, (19, 20)))
            self.assert_report(crop(db, RECORDING, 11, POINT), (37,), ((10, 0, 0, 0),), 37)

    def test_split_reads(self):
        with self.open_archive() as db:
            seed(db)
            split = SplitBody((8, 9, 10, 11), 1)
            send(db, 11, DeviceBlock(SCREEN, split))
            self.assertEqual(split.sizes, [1, 1, 1, 1, 0])
            self.assert_report(crop(db, RECORDING, 11, SCREEN), (8, 9, 10, 11),
                               ((11, 0, 0, 0), (11, 0, 1, 0), (11, 0, 0, 1), (11, 0, 1, 1)), 38)

    def test_invalid_block(self):
        with self.open_archive() as db:
            seed(db)
            outside = SplitBody((10, 11), 2)
            with self.assertRaises(RectangleError):
                send(db, 11, DeviceBlock(Rect(1, 0, 3, 1), outside))
            self.assertEqual(outside.sizes, [])
            self.assert_frame_unchanged(db)
            with self.assertRaisesRegex(PayloadError, 'extra bytes'):
                send(db, 11, block(RIGHT, (19, 20, 21)))
            self.assert_frame_unchanged(db)

    def test_input_error_retry(self):
        with self.open_archive() as db:
            seed(db)
            broken = BrokenBody()
            with self.assertRaisesRegex(OSError, 'input interrupted'):
                send(db, 11, block(POINT, (91,)), DeviceBlock(RIGHT, broken))
            self.assertEqual(broken.calls, 2)
            self.assert_frame_unchanged(db)
            self.assertEqual(send(db, 11, block(RIGHT, (19, 20))), PublishedFrame(RECORDING, 11))
            self.assert_report(crop(db, RECORDING, 11, SCREEN), (37, 19, 3, 20),
                               ((10, 0, 0, 0), (11, 0, 0, 0), (10, 0, 0, 1), (11, 0, 0, 1)), 79)

    def test_unsupported_mode_rejected_before_blocks(self):
        with self.open_archive() as db:
            seed(db)
            unread = (self.fail('frame blocks were consumed') for _ in range(1))
            with self.assertRaises(FrameModeError):
                capture(db, RECORDING, DeviceFrame(FrameHeader(11, 'unsupported'), unread))
            self.assert_frame_unchanged(db)
