from dataclasses import replace

from archive import FrameNotPublished, PublishedFrame, ResumeState, resume
from inspection import crop, inspect
from intake import BaseFrameError
from registration import (
    NamedRegions, RecordingIdentity, Rect, RectangleError, RegistrationConflict,
    ScreenSpec, UnknownRecording, register, require_recording,
)
from tests import BASE, BASE_ORIGINS, LEFT, RECORDING, RIGHT, SCREEN, RecordingCase, block, send, spec


class RegistrationTests(RecordingCase):
    def test_base_region(self):
        with self.open_archive() as db:
            registration = spec()
            self.assertEqual(register(db, registration), registration.identity)
            self.assertEqual(send(db, 10, block(SCREEN, BASE)), PublishedFrame(RECORDING, 10))
            report = inspect(db, RECORDING, 10, 'reading')
            self.assertEqual((report.recording_id, report.frame_no, report.rect), (RECORDING, 10, LEFT))
            self.assert_report(report, (37, 3), ((10, 0, 0, 0), (10, 0, 0, 1)), 40)

    def test_named_regions(self):
        with self.open_archive() as db:
            register(db, spec())
            send(db, 10, block(SCREEN, BASE))
            reading = inspect(db, RECORDING, 10, 'reading')
            alarm = inspect(db, RECORDING, 10, 'alarm')
            self.assert_report(reading, (37, 3), (BASE_ORIGINS[0], BASE_ORIGINS[2]), 40)
            self.assert_report(alarm, (2, 4), (BASE_ORIGINS[1], BASE_ORIGINS[3]), 6)
            self.assertEqual(reading, crop(db, RECORDING, 10, LEFT))
            self.assertEqual(alarm, crop(db, RECORDING, 10, RIGHT))

    def test_partial_base_retry(self):
        with self.open_archive() as db:
            register(db, spec())
            attempts = ((), (block(LEFT, (37, 3)),), (block(SCREEN, BASE), block(RIGHT, (2, 4))))
            for blocks in attempts:
                with self.subTest(block_count=len(blocks)), self.assertRaises(BaseFrameError):
                    send(db, 10, *blocks)
                self.assertEqual(resume(db, RECORDING), ResumeState(None, 10, None))
                with self.assertRaises(FrameNotPublished):
                    crop(db, RECORDING, 10, SCREEN)
            send(db, 10, block(SCREEN, BASE))
            self.assert_report(crop(db, RECORDING, 10, SCREEN), BASE, BASE_ORIGINS, 46)

    def test_repeat_and_conflict(self):
        original = spec()
        with self.open_archive() as db:
            self.assertEqual(register(db, original), original.identity)
            self.assertEqual(register(db, original), original.identity)
            conflicting = (
                replace(original, screen=ScreenSpec(3, 2, 10)),
                replace(original, screen=ScreenSpec(2, 3, 10)),
                replace(original, screen=ScreenSpec(2, 2, 11)),
                replace(original, identity=RecordingIdentity(RECORDING, 'spare')),
                replace(original, regions=NamedRegions({'reading': RIGHT, 'alarm': LEFT})),
            )
            for changed in conflicting:
                with self.subTest(changed=changed), self.assertRaises(RegistrationConflict):
                    register(db, changed)
                self.assertEqual(require_recording(db.connection, RECORDING), original)
            send(db, 10, block(SCREEN, BASE))
        with self.open_archive() as db:
            self.assert_report(inspect(db, RECORDING, 10, 'reading'), (37, 3), (BASE_ORIGINS[0], BASE_ORIGINS[2]), 40)

    def test_reopen_initialization(self):
        with self.open_archive() as db:
            original = spec()
            register(db, original)
            send(db, 10, block(SCREEN, BASE))
            previous = inspect(db, RECORDING, 10, 'alarm')
        with self.open_archive() as db:
            db.initialize()
            reordered = replace(original, regions=NamedRegions({'alarm': RIGHT, 'reading': LEFT}))
            self.assertEqual(register(db, reordered), original.identity)
            self.assertEqual(inspect(db, RECORDING, 10, 'alarm'), previous)
            self.assert_report(previous, (2, 4), (BASE_ORIGINS[1], BASE_ORIGINS[3]), 6)
            self.assertEqual(resume(db, RECORDING), ResumeState(10, 11, 10))

    def test_invalid_registration_has_no_persistent_identity(self):
        original = spec()
        with self.open_archive() as db:
            with self.assertRaises(ValueError):
                register(db, replace(original, screen=ScreenSpec(0, 2, 10)))
            with self.assertRaises(RectangleError):
                register(db, replace(original, regions=NamedRegions({'bad': Rect(1, 0, 3, 2)})))
            with self.assertRaises(UnknownRecording):
                resume(db, RECORDING)
            self.assertEqual(register(db, original), original.identity)
