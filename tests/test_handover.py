import json
import resource
import sqlite3
import subprocess
import sys
from pathlib import Path

from archive import ArchiveDatabase, FrameNotPublished, PublishedFrame, ResumeState, resume
from inspection import crop, inspect
from instrument_demo import LARGE_RECORDING, LARGE_REGION, collect, large_frames, large_recording_spec, report_data
from intake import DeviceFrame, DeviceStream, FrameHeader, FrameSequenceError, capture
from registration import Rect, RectangleError, UnknownRecording, UnknownRegion, register
from tests import BASE, BASE_ORIGINS, LEFT, RECORDING, RIGHT, SCREEN, RecordingCase, block, seed, send, spec


POINT = Rect(0, 0, 1, 1)
MAX_EXTRA_RSS = 16 * 1024 * 1024
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _peak_rss_bytes():
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == 'darwin' else peak * 1024


def _large_worker(phase, path):
    idle = _peak_rss_bytes()
    with ArchiveDatabase(path) as db:
        if phase == 'capture':
            register(db, large_recording_spec())
            with DeviceStream(large_frames()) as device:
                published = collect(db, LARGE_RECORDING, device)
        elif phase == 'read':
            current = inspect(db, LARGE_RECORDING, 11, 'boundary')
            original = crop(db, LARGE_RECORDING, 10, LARGE_REGION)
            limit_area = crop(db, LARGE_RECORDING, 11, Rect(0, 0, 64, 64))
            next_frame = resume(db, LARGE_RECORDING).next_frame
        else:
            raise ValueError(f'unknown worker phase {phase!r}')
    peak = _peak_rss_bytes()
    memory = {'idle_rss': idle, 'peak_rss': peak, 'extra_rss': peak - idle}
    if phase == 'capture':
        output = {'published': published, 'memory': memory}
    else:
        output = {
            'current': report_data(current), 'original': report_data(original),
            'limit_area': report_data(limit_area), 'next_frame': next_frame, 'memory': memory,
        }
    print(json.dumps(output))


class HandoverTests(RecordingCase):
    def test_reopen(self):
        with self.open_archive() as db, DeviceStream((DeviceFrame(FrameHeader(10), iter((block(SCREEN, BASE),))),)) as device:
            register(db, spec())
            self.assertEqual(collect(db, RECORDING, device), [10])
        self.assertIsNone(device.next_frame())
        with self.open_archive() as db:
            old = inspect(db, RECORDING, 10, 'reading')
            self.assert_report(old, (37, 3), ((10, 0, 0, 0), (10, 0, 0, 1)), 40)
            self.assertEqual(resume(db, RECORDING), ResumeState(10, 11, 10))
            send(db, 11, block(RIGHT, (19, 20)))
        with self.open_archive() as db:
            self.assertEqual(inspect(db, RECORDING, 10, 'reading'), old)
            self.assert_report(crop(db, RECORDING, 11, SCREEN), (37, 19, 3, 20),
                               ((10, 0, 0, 0), (11, 0, 0, 0), (10, 0, 0, 1), (11, 0, 0, 1)), 79)

    def test_empty_recording(self):
        with self.open_archive() as db:
            register(db, spec())
        with self.open_archive() as db:
            self.assertEqual(resume(db, RECORDING), ResumeState(None, 10, None))
            with self.assertRaises(UnknownRecording):
                resume(db, 'unknown')
            with self.assertRaises(FrameNotPublished):
                crop(db, RECORDING, 10, SCREEN)

    def test_resume_sequence(self):
        with self.open_archive() as db:
            seed(db)
            send(db, 11, block(POINT, (82,)))
        with self.open_archive() as db:
            self.assertEqual(resume(db, RECORDING), ResumeState(11, 12, 11))
            for invalid_no in (11, 13):
                unread = (self.fail('invalid frame consumed its blocks') for _ in range(1))
                with self.subTest(frame_no=invalid_no), self.assertRaises(FrameSequenceError):
                    capture(db, RECORDING, DeviceFrame(FrameHeader(invalid_no), unread))
            self.assertEqual(resume(db, RECORDING), ResumeState(11, 12, 11))
            self.assertEqual(send(db, 12, block(RIGHT, (19, 20))), PublishedFrame(RECORDING, 12))
            self.assert_report(crop(db, RECORDING, 12, SCREEN), (82, 19, 3, 20),
                               ((11, 0, 0, 0), (12, 0, 0, 0), (10, 0, 0, 1), (12, 0, 0, 1)), 124)

    def test_history_after_resume(self):
        with self.open_archive() as db:
            seed(db)
            original = crop(db, RECORDING, 10, SCREEN)
        with self.open_archive() as db:
            send(db, 11, block(POINT, (91,)))
        with self.open_archive() as db:
            self.assertEqual(crop(db, RECORDING, 10, SCREEN), original)
            self.assert_report(original, BASE, BASE_ORIGINS, 46)
            self.assert_report(crop(db, RECORDING, 11, SCREEN), (91, 2, 3, 4),
                               ((11, 0, 0, 0), *BASE_ORIGINS[1:]), 100)

    def test_commit_failure_retry(self):
        with self.open_archive() as db:
            seed(db)
        with self.open_archive() as writer, self.open_archive() as observer:
            def deny_commit(action, name, detail, database, source):
                if action == sqlite3.SQLITE_TRANSACTION and name == 'COMMIT':
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            def before_commit():
                self.assertEqual(resume(observer, RECORDING), ResumeState(10, 11, 10))
                with self.assertRaises(FrameNotPublished):
                    crop(observer, RECORDING, 11, POINT)
                writer.connection.set_authorizer(deny_commit)

            try:
                with self.assertRaises(sqlite3.DatabaseError) as raised:
                    send(writer, 11, block(POINT, (91,)), before_commit=before_commit)
                self.assertEqual(raised.exception.sqlite_errorcode, sqlite3.SQLITE_AUTH)
            finally:
                writer.connection.set_authorizer(None)
        with self.open_archive() as db:
            self.assertEqual(resume(db, RECORDING), ResumeState(10, 11, 10))
            with self.assertRaises(FrameNotPublished):
                crop(db, RECORDING, 11, POINT)
            self.assert_report(crop(db, RECORDING, 10, SCREEN), BASE, BASE_ORIGINS, 46)
            self.assertEqual(send(db, 11, block(POINT, (82,))), PublishedFrame(RECORDING, 11))
        with self.open_archive() as db:
            self.assertEqual(resume(db, RECORDING), ResumeState(11, 12, 11))
            self.assert_report(crop(db, RECORDING, 11, POINT), (82,), ((11, 0, 0, 0),), 82)
            with self.assertRaises(FrameSequenceError):
                send(db, 11, block(POINT, (99,)))
            self.assert_report(crop(db, RECORDING, 11, POINT), (82,), ((11, 0, 0, 0),), 82)

    def test_first_commit_retry(self):
        with self.open_archive() as db:
            register(db, spec())

        def interrupt_publication():
            raise OSError('publication interrupted')

        with self.open_archive() as db:
            with self.assertRaisesRegex(OSError, 'publication interrupted'):
                send(db, 10, block(SCREEN, (8, 9, 10, 11)), before_commit=interrupt_publication)
        with self.open_archive() as db:
            self.assertEqual(resume(db, RECORDING), ResumeState(None, 10, None))
            with self.assertRaises(FrameNotPublished):
                crop(db, RECORDING, 10, SCREEN)
            self.assertEqual(send(db, 10, block(SCREEN, BASE)), PublishedFrame(RECORDING, 10))
        with self.open_archive() as db:
            self.assertEqual(resume(db, RECORDING), ResumeState(10, 11, 10))
            with self.assertRaises(FrameSequenceError):
                send(db, 10, block(SCREEN, (8, 9, 10, 11)))
            self.assert_report(crop(db, RECORDING, 10, SCREEN), BASE, BASE_ORIGINS, 46)

    def test_crop_out_of_screen(self):
        with self.open_archive() as db:
            seed(db)
        with self.open_archive() as db:
            with self.assertRaises(RectangleError):
                crop(db, RECORDING, 10, Rect(1, 0, 3, 2))
            self.assert_report(crop(db, RECORDING, 10, RIGHT), (2, 4), (BASE_ORIGINS[1], BASE_ORIGINS[3]), 6)

    def test_bounds_errors(self):
        with self.open_archive() as db:
            seed(db)
        with self.open_archive() as db:
            for invalid in (Rect(-1, 0, 1, 1), Rect(0, -1, 1, 1), Rect(0, 0, 3, 1),
                            Rect(0, 0, 1, 3), Rect(1, 0, 1, 1), Rect(0, 1, 1, 1)):
                with self.subTest(rect=invalid), self.assertRaises(RectangleError):
                    crop(db, RECORDING, 10, invalid)
            self.assert_report(crop(db, RECORDING, 10, Rect(1, 1, 2, 2)), (4,), ((10, 0, 1, 1),), 4)

    def test_exact_total(self):
        with self.open_archive() as db:
            seed(db, (255, 0, 255, 9))
        with self.open_archive() as db:
            with self.assertRaises(RectangleError):
                crop(db, RECORDING, 10, Rect(1, 0, 3, 2))
            self.assert_report(crop(db, RECORDING, 10, LEFT), (255, 255), (BASE_ORIGINS[0], BASE_ORIGINS[2]), 510)
            self.assert_report(crop(db, RECORDING, 10, Rect(1, 0, 2, 1)), (0,), ((10, 0, 1, 0),), 0)

    def test_lookup_errors(self):
        with self.open_archive() as db:
            seed(db)
        with self.open_archive() as db:
            with self.assertRaises(UnknownRecording):
                crop(db, 'unknown', 10, SCREEN)
            with self.assertRaises(FrameNotPublished):
                crop(db, RECORDING, 11, SCREEN)
            with self.assertRaises(UnknownRegion):
                inspect(db, RECORDING, 10, 'unregistered')
            self.assert_report(crop(db, RECORDING, 10, SCREEN), BASE, BASE_ORIGINS, 46)

    def run_large_phase(self, phase):
        argv = [sys.executable, '-B', '-m', 'tests.test_handover', phase, str(self.path)]
        result = subprocess.run(argv, cwd=PROJECT_ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_large_permanent(self):
        captured = self.run_large_phase('capture')
        received = self.run_large_phase('read')
        for phase, measurement in (('capture', captured['memory']), ('read', received['memory'])):
            self.assertGreater(measurement['idle_rss'], 0)
            self.assertGreaterEqual(measurement['peak_rss'], measurement['idle_rss'])
            self.assertEqual(measurement['extra_rss'], measurement['peak_rss'] - measurement['idle_rss'])
            self.assertLessEqual(measurement['extra_rss'], MAX_EXTRA_RSS, (phase, measurement))
        self.assertEqual(captured['published'], [10, 11])
        self.assertEqual(received['next_frame'], 12)
        expected_current = {
            'pixels': [251, 82, 83, 84, 255, 0, 254, 85, 91, 92, 93, 3],
            'origins': [
                [10, 0, 126, 127], [11, 0, 0, 0], [11, 0, 1, 0], [11, 0, 2, 0], [10, 0, 130, 127], [10, 0, 131, 127],
                [10, 0, 126, 128], [11, 0, 0, 1], [11, 1, 0, 0], [11, 1, 1, 0], [11, 1, 2, 0], [10, 0, 131, 128],
            ],
            'gray_total': 1373,
        }
        expected_original = {
            'pixels': [251, 252, 253, 254, 255, 0, 254, 255, 0, 1, 2, 3],
            'origins': [[10, 0, x, y] for y in (127, 128) for x in range(126, 132)],
            'gray_total': 1780,
        }
        self.assertEqual(received['current'], expected_current)
        self.assertEqual(received['original'], expected_original)
        # The boundary patches never intersect this crop, and x + 3*y stays below 256 here.
        expected_limit_pixels = [x + 3 * y for y in range(64) for x in range(64)]
        self.assertEqual(received['limit_area'], {
            'pixels': expected_limit_pixels,
            'origins': [[10, 0, x, y] for y in range(64) for x in range(64)],
            'gray_total': 516096,
        })
        print('V1 RSS ' + json.dumps({'capture': captured['memory'], 'read': received['memory']}))
        print('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': 'v6.large_permanent',
            'input': {
                'screen': [8192, 8192], 'base_gray': '(x + 3*y) % 256',
                'patches': [
                    {'rect': [127, 127, 130, 129], 'pixels': [82, 83, 84, 85, 86, 87]},
                    {'rect': [128, 128, 131, 129], 'pixels': [91, 92, 93]},
                ],
                'crop': [126, 127, 132, 129],
            },
            'expected': {'published': [10, 11], 'current': expected_current},
            'actual': {'published': captured['published'], 'current': received['current']},
            'downstream_expected': {'original': expected_original, 'next_frame': 12},
            'downstream_actual': {'original': received['original'], 'next_frame': received['next_frame']},
            'code_paths': ['intake/session.py', 'reconstruction/tiles.py', 'reconstruction/staging.py',
                           'archive/blocks.py', 'archive/resume.py', 'inspection/crops.py'],
            'oracle': 'Hand-calculated two intersecting rows with explicit source coordinates and fixed totals.',
        }))


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit('usage: python3 -B -m tests.test_handover capture|read ARCHIVE')
    _large_worker(sys.argv[1], Path(sys.argv[2]))
