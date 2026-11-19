from io import BytesIO

from archive import ArchiveDatabase, FrameNotPublished, ResumeState, resume
from inspection import crop, crop_with_moves
from intake import (
    DeviceBlock,
    DeviceFrame,
    FrameHeader,
    MoveRect,
    capture,
)
from registration import NamedRegions, RectangleError, RecordingIdentity, RecordingSpec, Rect, ScreenSpec, register
from tests import RecordingCase, block, send


RECORDING = "move-case"
SCREEN = Rect(0, 0, 6, 6)


def _spec(width=6, height=6, first=10):
    return RecordingSpec(
        RecordingIdentity(RECORDING, "ink-move"),
        ScreenSpec(width, height, first),
        NamedRegions({"all": Rect(0, 0, width, height)}),
    )


def _base(db, pixels, width=6, height=6, first=10):
    register(db, _spec(width, height, first))
    send_frame(db, first, DeviceBlock(Rect(0, 0, width, height), BytesIO(bytes(pixels))))


def send_frame(db, frame_no, *entries, before_commit=None):
    return capture(
        db, RECORDING, DeviceFrame(FrameHeader(frame_no), iter(entries)),
        before_commit=before_commit,
    )


def move_tuple(evidence):
    return None if evidence is None else (
        evidence.frame_no, evidence.command_index, evidence.x, evidence.y
    )


def origin_tuple(origin):
    return origin.frame_no, origin.block_index, origin.x, origin.y


class MoveValidationTests(RecordingCase):
    def test_base_frame_rejects_move(self):
        with self.open_archive() as db:
            register(db, _spec())
            with self.assertRaises(Exception):
                capture(
                    db, RECORDING,
                    DeviceFrame(FrameHeader(10), iter((
                        DeviceBlock(SCREEN, BytesIO(bytes(range(36)))),
                        MoveRect(Rect(0, 0, 1, 1), Rect(1, 1, 2, 2)),
                    ))),
                )
            self.assertEqual(resume(db, RECORDING), ResumeState(None, 10, None))

    def test_illegal_rectangles(self):
        with self.open_archive() as db:
            _base(db, range(36))
            cases = (
                MoveRect(Rect(-1, 0, 1, 1), Rect(0, 0, 2, 1)),       # source outside
                MoveRect(Rect(0, 0, 2, 1), Rect(0, 0, 7, 1)),         # target outside
                MoveRect(Rect(0, 0, 2, 1), Rect(0, 0, 2, 2)),         # unequal size
                MoveRect(Rect(0, 0, 0, 1), Rect(0, 0, 0, 1)),         # empty
            )
            for move in cases:
                with self.subTest(move=move), self.assertRaises(RectangleError):
                    send_frame(db, 11, move)
            self.assertEqual(resume(db, RECORDING), ResumeState(10, 11, 10))

    def test_unsupported_entry_type(self):
        with self.open_archive() as db:
            _base(db, range(36))
            with self.assertRaisesRegex(ValueError, "unsupported frame entry"):
                send_frame(db, 11, object())
            self.assertEqual(resume(db, RECORDING), ResumeState(10, 11, 10))

    def test_single_entry_iterator_consumed_once(self):
        consumed = []

        def entries():
            consumed.append("start")
            yield block(Rect(0, 0, 1, 1), (7,))
            yield MoveRect(Rect(0, 0, 2, 1), Rect(2, 0, 4, 1))
            consumed.append("end")

        with self.open_archive() as db:
            _base(db, range(36))
            capture(db, RECORDING, DeviceFrame(FrameHeader(11), entries()))
            self.assertEqual(consumed, ["start", "end"])


class MoveSemanticsTests(RecordingCase):
    def _report(self, db, frame, rect=SCREEN):
        return crop_with_moves(db, RECORDING, frame, rect)

    def test_identical_source_target_records_move(self):
        with self.open_archive() as db:
            _base(db, range(36))
            send_frame(db, 11, MoveRect(Rect(1, 1, 3, 3), Rect(1, 1, 3, 3)))
            report = self._report(db, 11, Rect(1, 1, 3, 3))
            # Grays and block origins are unchanged; every pixel gains move evidence.
            self.assertEqual(list(report.pixels), [7, 8, 13, 14])
            self.assertEqual(
                [origin_tuple(o) for o in report.origins],
                [(10, 0, 1, 1), (10, 0, 2, 1), (10, 0, 1, 2), (10, 0, 2, 2)],
            )
            self.assertEqual(
                [move_tuple(m) for m in report.moves],
                [(11, 0, 1, 1), (11, 0, 2, 1), (11, 0, 1, 2), (11, 0, 2, 2)],
            )
    def test_overlap_right_uses_command_start_snapshot(self):
        # 1x4 row; shift [0..3) onto [1..4), source and target overlap.
        with self.open_archive() as db:
            register(db, _spec(width=4, height=1))
            send_frame(db, 10, DeviceBlock(Rect(0, 0, 4, 1), BytesIO(bytes((1, 2, 3, 4)))))
            send_frame(db, 11, MoveRect(Rect(0, 0, 3, 1), Rect(1, 0, 4, 1)))
            report = crop_with_moves(db, RECORDING, 11, Rect(0, 0, 4, 1))
            self.assertEqual(list(report.pixels), [1, 1, 2, 3])
            self.assertEqual([move_tuple(m) for m in report.moves],
                             [None, (11, 0, 0, 0), (11, 0, 1, 0), (11, 0, 2, 0)])

    def test_overlap_left_and_vertical_directions(self):
        # Horizontal leftward overlap on one screen.
        with self.open_archive() as db:
            register(db, _spec(width=4, height=1))
            send_frame(db, 10, DeviceBlock(Rect(0, 0, 4, 1), BytesIO(bytes((1, 2, 3, 4)))))
            send_frame(db, 11, MoveRect(Rect(1, 0, 4, 1), Rect(0, 0, 3, 1)))
            row = crop_with_moves(db, RECORDING, 11, Rect(0, 0, 4, 1))
            self.assertEqual(list(row.pixels), [2, 3, 4, 4])
            self.assertEqual([move_tuple(m) for m in row.moves],
                             [(11, 0, 1, 0), (11, 0, 2, 0), (11, 0, 3, 0), None])
            # Pixel 3 is untouched, so it keeps its original block origin and no evidence.
        # Downward vertical overlap on a fresh screen.
        vertical_path = self.path.with_name("vertical.sqlite")
        self.addCleanup(vertical_path.unlink, missing_ok=True)
        with ArchiveDatabase(vertical_path) as db:
            register(db, _spec(width=1, height=4))
            send_frame(db, 10, DeviceBlock(Rect(0, 0, 1, 4), BytesIO(bytes((0, 4, 8, 12)))))
            # Move rows 0..2 down onto rows 1..3: [0,0,4,8].
            send_frame(db, 11, MoveRect(Rect(0, 0, 1, 3), Rect(0, 1, 1, 4)))
            col = crop_with_moves(db, RECORDING, 11, Rect(0, 0, 1, 4))
            self.assertEqual(list(col.pixels), [0, 0, 4, 8])
            self.assertEqual([move_tuple(m) for m in col.moves],
                             [None, (11, 0, 0, 0), (11, 0, 0, 1), (11, 0, 0, 2)])

    def test_cross_tile_horizontal_overlap(self):
        edge = 128
        width = edge + 2
        with self.open_archive() as db:
            register(db, _spec(width=width, height=2))
            base = bytes((x % 256 for x in range(width * 2)))
            send_frame(db, 10, DeviceBlock(Rect(0, 0, width, 2), BytesIO(base)))
            # Rightward shift crossing the tile boundary on both rows.
            send_frame(db, 11, MoveRect(Rect(edge - 2, 0, edge + 1, 2), Rect(edge - 1, 0, edge + 2, 2)))
            report = crop_with_moves(db, RECORDING, 11, Rect(edge - 2, 0, width, 2))
            expected = [
                (edge - 2) % 256, (edge - 2) % 256, (edge - 1) % 256, (edge) % 256,
                (width + edge - 2) % 256, (width + edge - 2) % 256,
                (width + edge - 1) % 256, (width + edge) % 256,
            ]
            self.assertEqual(list(report.pixels), expected)
            moves = [move_tuple(m) for m in report.moves]
            self.assertEqual(moves[0], None)
            self.assertEqual(moves[1], (11, 0, edge - 2, 0))
            self.assertEqual(moves[4], None)
            self.assertEqual(moves[7], (11, 0, edge, 1))

    def test_cross_tile_vertical_overlap(self):
        edge = 128
        height = edge + 2
        with self.open_archive() as db:
            register(db, _spec(width=2, height=height))
            base = bytes(((x + 3 * y) % 256 for y in range(height) for x in range(2)))
            send_frame(db, 10, DeviceBlock(Rect(0, 0, 2, height), BytesIO(base)))
            # Downward shift by one row crossing the horizontal tile boundary.
            send_frame(db, 11, MoveRect(Rect(0, edge - 2, 2, edge + 1),
                                        Rect(0, edge - 1, 2, edge + 2)))
            report = crop_with_moves(db, RECORDING, 11, Rect(0, edge - 2, 2, height))
            expected = [
                (3 * (edge - 2)) % 256, (1 + 3 * (edge - 2)) % 256,      # row 126 untouched
                (3 * (edge - 2)) % 256, (1 + 3 * (edge - 2)) % 256,      # 127 <- 126
                (3 * (edge - 1)) % 256, (1 + 3 * (edge - 1)) % 256,      # 128 <- 127
                (3 * edge) % 256, (1 + 3 * edge) % 256,                  # 129 <- 128
            ]
            self.assertEqual(list(report.pixels), expected)
            moves = [move_tuple(m) for m in report.moves]
            self.assertEqual(moves[0:2], [None, None])
            self.assertEqual(moves[2], (11, 0, 0, edge - 2))   # target 127 samples 126
            self.assertEqual(moves[6], (11, 0, 0, edge))       # target 129 samples 128

    def test_snapshot_includes_earlier_same_frame_command(self):
        # Two moves in one frame; the second must see the first move's result.
        with self.open_archive() as db:
            register(db, _spec(width=4, height=1))
            send_frame(db, 10, DeviceBlock(Rect(0, 0, 4, 1), BytesIO(bytes((1, 2, 3, 4)))))
            send_frame(
                db, 11,
                MoveRect(Rect(0, 0, 1, 1), Rect(1, 0, 2, 1)),   # idx 0
                MoveRect(Rect(1, 0, 2, 1), Rect(2, 0, 3, 1)),   # idx 1 reads moved cell
            )
            report = crop_with_moves(db, RECORDING, 11, Rect(0, 0, 4, 1))
            self.assertEqual(list(report.pixels), [1, 1, 1, 4])
            self.assertEqual([move_tuple(m) for m in report.moves],
                             [None, (11, 0, 0, 0), (11, 1, 1, 0), None])

    def test_wide_rightward_move_spans_many_bands(self):
        # Wider than a processing band (BAND_TILES tiles) so source tiles are
        # reused across bands; pristine content must survive candidate writes.
        width, height = 400, 2
        with self.open_archive() as db:
            register(db, _spec(width=width, height=height))
            base = bytes((x % 256 for y in range(height) for x in range(width)))
            send_frame(db, 10, DeviceBlock(Rect(0, 0, width, height), BytesIO(base)))
            send_frame(db, 11, MoveRect(Rect(0, 0, width - 1, height),
                                        Rect(1, 0, width, height)))
            report = crop_with_moves(db, RECORDING, 11, Rect(0, 0, width, 1))
            expected = [0] + [(x - 1) % 256 for x in range(1, width)]
            self.assertEqual(list(report.pixels), expected)
            for boundary in (127, 128, 255, 256, 383):
                edge = crop_with_moves(db, RECORDING, 11, Rect(boundary, 0, boundary + 2, 1))
                self.assertEqual(list(edge.pixels),
                                 [(boundary - 1) % 256, boundary % 256])

    def test_tall_downward_move_spans_many_bands(self):
        width, height = 2, 400
        with self.open_archive() as db:
            register(db, _spec(width=width, height=height))
            base = bytes(((x + 3 * y) % 256 for y in range(height) for x in range(width)))
            send_frame(db, 10, DeviceBlock(Rect(0, 0, width, height), BytesIO(base)))
            send_frame(db, 11, MoveRect(Rect(0, 0, width, height - 1),
                                        Rect(0, 1, width, height)))
            for y in range(1, height):
                report = crop_with_moves(db, RECORDING, 11, Rect(0, y, width, y + 1))
                self.assertEqual(list(report.pixels),
                                 [(3 * (y - 1)) % 256, (1 + 3 * (y - 1)) % 256])

    def test_removes_keeps_origins_replaces_evidence(self):
        with self.open_archive() as db:
            _base(db, range(36))
            send_frame(db, 11, MoveRect(Rect(0, 0, 2, 1), Rect(2, 0, 4, 1)))
            first = self._report(db, 11, Rect(2, 0, 4, 1))
            self.assertEqual([move_tuple(m) for m in first.moves],
                             [(11, 0, 0, 0), (11, 0, 1, 0)])
            # Move again from the already-moved target.
            send_frame(db, 12, MoveRect(Rect(2, 0, 4, 1), Rect(0, 0, 2, 1)))
            second = crop_with_moves(db, RECORDING, 12, Rect(0, 0, 2, 1))
            # Original block origin is preserved, evidence now points at frame 12.
            self.assertEqual([origin_tuple(o) for o in second.origins],
                             [(10, 0, 0, 0), (10, 0, 1, 0)])
            self.assertEqual([move_tuple(m) for m in second.moves],
                             [(12, 0, 2, 0), (12, 0, 3, 0)])

    def test_direct_write_clears_evidence_even_same_value(self):
        with self.open_archive() as db:
            register(db, _spec(width=2, height=1))
            send_frame(db, 10, DeviceBlock(Rect(0, 0, 2, 1), BytesIO(bytes((5, 9)))))
            send_frame(db, 11, MoveRect(Rect(0, 0, 1, 1), Rect(1, 0, 2, 1)))
            moved = crop_with_moves(db, RECORDING, 11, Rect(0, 0, 2, 1))
            self.assertEqual(list(moved.pixels), [5, 5])
            self.assertIsNotNone(moved.moves[1])
            # Same-value direct write must still clear evidence and refresh origin.
            send_frame(db, 12, DeviceBlock(Rect(1, 0, 2, 1), BytesIO(bytes((5,)))))
            report = crop_with_moves(db, RECORDING, 12, Rect(0, 0, 2, 1))
            self.assertEqual(list(report.pixels), [5, 5])
            self.assertIsNone(report.moves[1])
            self.assertEqual(origin_tuple(report.origins[1]), (12, 0, 0, 0))

    def test_untouched_positions_keep_evidence(self):
        with self.open_archive() as db:
            _base(db, range(36))
            send_frame(db, 11, MoveRect(Rect(0, 0, 3, 1), Rect(1, 0, 4, 1)))
            send_frame(db, 12, DeviceBlock(Rect(4, 0, 5, 1), BytesIO(bytes((99,)))))
            report = crop_with_moves(db, RECORDING, 12, Rect(0, 0, 6, 1))
            self.assertEqual([move_tuple(m) for m in report.moves],
                             [None, (11, 0, 0, 0), (11, 0, 1, 0),
                              (11, 0, 2, 0), None, None])

    def test_command_index_counts_all_entries(self):
        with self.open_archive() as db:
            _base(db, range(36))
            send_frame(
                db, 11,
                DeviceBlock(Rect(0, 0, 1, 1), BytesIO(bytes((7,)))),
                MoveRect(Rect(0, 0, 1, 1), Rect(1, 0, 2, 1)),
                MoveRect(Rect(1, 0, 2, 1), Rect(2, 0, 3, 1)),
            )
            report = crop_with_moves(db, RECORDING, 11, Rect(0, 0, 3, 1))
            self.assertEqual([move_tuple(m) for m in report.moves],
                             [None, (11, 1, 0, 0), (11, 2, 1, 0)])


class MoveFailureTests(RecordingCase):
    def test_invalid_command_publishes_nothing_and_retries(self):
        with self.open_archive() as db:
            _base(db, range(36))
            with self.assertRaises(RectangleError):
                send_frame(db, 11, MoveRect(Rect(0, 0, 2, 1), Rect(0, 0, 3, 1)))
            self.assertEqual(resume(db, RECORDING), ResumeState(10, 11, 10))
            with self.assertRaises(FrameNotPublished):
                crop_with_moves(db, RECORDING, 11, SCREEN)
            # Same frame number is reusable with valid input.
            send_frame(db, 11, MoveRect(Rect(0, 0, 2, 1), Rect(2, 0, 4, 1)))
            report = crop_with_moves(db, RECORDING, 11, Rect(2, 0, 4, 1))
            self.assertEqual(list(report.pixels), [0, 1])

    def test_command_stream_exception_publishes_nothing(self):
        def failing_entries():
            yield MoveRect(Rect(0, 0, 1, 1), Rect(1, 0, 2, 1))
            raise OSError("command stream broke")

        with self.open_archive() as db:
            _base(db, range(36))
            with self.assertRaisesRegex(OSError, "command stream broke"):
                capture(db, RECORDING, DeviceFrame(FrameHeader(11), failing_entries()))
            self.assertEqual(resume(db, RECORDING), ResumeState(10, 11, 10))
            with self.assertRaises(FrameNotPublished):
                crop_with_moves(db, RECORDING, 11, SCREEN)
            # Retry the same frame number with valid input.
            send_frame(db, 11, MoveRect(Rect(0, 0, 2, 1), Rect(2, 0, 4, 1)))
            self.assertEqual(resume(db, RECORDING), ResumeState(11, 12, 11))

    def test_source_error_after_valid_command_publishes_nothing(self):
        class ExplodingBody:
            def read(self, size):
                raise OSError("device input interrupted")

        with self.open_archive() as db:
            _base(db, range(36))
            with self.assertRaises(OSError):
                send_frame(
                    db, 11,
                    MoveRect(Rect(0, 0, 1, 1), Rect(1, 0, 2, 1)),
                    DeviceBlock(Rect(0, 0, 1, 1), ExplodingBody()),
                )
            self.assertEqual(resume(db, RECORDING), ResumeState(10, 11, 10))
            with self.assertRaises(FrameNotPublished):
                crop_with_moves(db, RECORDING, 11, SCREEN)
            # Earlier frame still fully claimable.
            old = crop(db, RECORDING, 10, SCREEN)
            self.assertEqual(list(old.pixels), list(range(36)))

    def test_commit_failure_then_retry(self):
        import sqlite3

        with self.open_archive() as db:
            _base(db, range(36))
        with self.open_archive() as writer, self.open_archive() as observer:
            def deny_commit(action, name, detail, database, source):
                if action == sqlite3.SQLITE_TRANSACTION and name == "COMMIT":
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            def before_commit():
                self.assertEqual(resume(observer, RECORDING), ResumeState(10, 11, 10))
                with self.assertRaises(FrameNotPublished):
                    crop_with_moves(observer, RECORDING, 11, SCREEN)
                writer.connection.set_authorizer(deny_commit)

            try:
                with self.assertRaises(sqlite3.DatabaseError) as raised:
                    send_frame(
                        writer, 11,
                        MoveRect(Rect(0, 0, 2, 1), Rect(2, 0, 4, 1)),
                        before_commit=before_commit,
                    )
                self.assertEqual(raised.exception.sqlite_errorcode, sqlite3.SQLITE_AUTH)
            finally:
                writer.connection.set_authorizer(None)
        with self.open_archive() as db:
            self.assertEqual(resume(db, RECORDING), ResumeState(10, 11, 10))
            send_frame(db, 11, MoveRect(Rect(0, 0, 2, 1), Rect(2, 0, 4, 1)))
            report = crop_with_moves(db, RECORDING, 11, Rect(2, 0, 4, 1))
            self.assertEqual(list(report.pixels), [0, 1])
            self.assertEqual([move_tuple(m) for m in report.moves],
                             [(11, 0, 0, 0), (11, 0, 1, 0)])


class MoveLookupTests(RecordingCase):
    def test_unknown_recording_frame_and_rect_share_crop_errors(self):
        with self.open_archive() as db:
            _base(db, range(36))
            from registration import UnknownRecording

            with self.assertRaises(UnknownRecording):
                crop_with_moves(db, "unknown", 10, SCREEN)
            with self.assertRaises(FrameNotPublished):
                crop_with_moves(db, RECORDING, 11, SCREEN)
            with self.assertRaises(RectangleError):
                crop_with_moves(db, RECORDING, 10, Rect(0, 0, 7, 1))
            with self.assertRaises(RectangleError):
                crop_with_moves(db, RECORDING, 10, "not-a-rect")

    def test_v1_archive_returns_all_none_evidence(self):
        with self.open_archive() as db:
            # Build a pure V1 archive using only direct blocks.
            register(db, _spec())
            send_frame(db, 10, DeviceBlock(SCREEN, BytesIO(bytes(range(36)))))
            send_frame(db, 11, DeviceBlock(Rect(0, 0, 1, 1), BytesIO(bytes((77,)))))
            report = crop_with_moves(db, RECORDING, 11, SCREEN)
            self.assertEqual(len(report.moves), 36)
            self.assertTrue(all(m is None for m in report.moves))
            self.assertEqual(report.pixels, crop(db, RECORDING, 11, SCREEN).pixels)

    def test_legacy_v1_tile_revision_reads_as_none_evidence(self):
        from reconstruction.tiles import TILE_EDGE, Tile, _ROW_LENGTH, _RUN
        # Hand-build a legacy V1 revision: pixels + origin rows only, no marker.
        origin = (0, TILE_EDGE, 10, 0, 0, 0)  # one full-row origin run
        encoded = bytearray((i % 256 for i in range(TILE_EDGE * TILE_EDGE)))
        for _y in range(TILE_EDGE):
            row = _RUN.pack(*origin)
            encoded.extend(_ROW_LENGTH.pack(len(row)))
            encoded.extend(row)
        legacy = Tile.from_bytes(bytes(encoded))
        self.assertEqual(tuple(legacy.read_move_span(0, 0, TILE_EDGE)), (None,) * TILE_EDGE)
        self.assertEqual(tuple(legacy.read_move_span(40, 7, 12)), (None,) * 12)

    def test_v1_history_beside_v2_move_keeps_none_evidence(self):
        with self.open_archive() as db:
            _base(db, range(36))
            send_frame(db, 11, MoveRect(Rect(0, 0, 2, 1), Rect(2, 0, 4, 1)))
        with self.open_archive() as db:
            # The older frame predates the command and stays all-None.
            old = crop_with_moves(db, RECORDING, 10, SCREEN)
            self.assertTrue(all(m is None for m in old.moves))
            # The move frame carries evidence with absolute sample points.
            new = crop_with_moves(db, RECORDING, 11, Rect(2, 0, 4, 1))
            self.assertEqual([move_tuple(m) for m in new.moves],
                             [(11, 0, 0, 0), (11, 0, 1, 0)])
            # Original crop interface still works on both frames.
            self.assertEqual(crop(db, RECORDING, 10, SCREEN).pixels, old.pixels)

    def test_reopen_after_stop_keeps_history(self):
        with self.open_archive() as db:
            _base(db, range(36))
            send_frame(db, 11, MoveRect(Rect(0, 0, 3, 1), Rect(1, 0, 4, 1)))
        # Reopen with a fresh connection, claim an old and a new frame.
        with self.open_archive() as db:
            base = crop_with_moves(db, RECORDING, 10, SCREEN)
            self.assertTrue(all(m is None for m in base.moves))
            moved = crop_with_moves(db, RECORDING, 11, Rect(0, 0, 4, 1))
            self.assertEqual(list(moved.pixels), [0, 0, 1, 2])
            # Continue collecting after the restart.
            send_frame(db, 12, DeviceBlock(Rect(0, 0, 1, 1), BytesIO(bytes((50,)))))
            latest = crop_with_moves(db, RECORDING, 12, Rect(0, 0, 4, 1))
            self.assertEqual(list(latest.pixels), [50, 0, 1, 2])
            self.assertEqual(move_tuple(latest.moves[1]), (11, 0, 0, 0))
