import json
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from archive import ArchiveDatabase, resume
from inspection import RegionReport, crop_with_moves, inspect
from intake import DeviceBlock, DeviceFrame, DeviceStream, FrameHeader, MoveRect, capture
from registration import NamedRegions, RecordingIdentity, RecordingSpec, Rect, ScreenSpec, register


LARGE_RECORDING = 'chamber-large'
LARGE_WIDTH = LARGE_HEIGHT = 8192
LARGE_REGION = Rect(126, 127, 132, 129)
_RAMP = bytes(range(256))


class PatternBody:
    """Generate screen bytes in row order with gray value (x + 3*y) % 256."""

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.length = width * height
        self.position = 0

    def read(self, size: int) -> bytes:
        if self.position == self.length:
            return b''
        y, x = divmod(self.position, self.width)
        count = min(size, self.width - x, self.length - self.position)
        phase = (x + 3 * y) % 256
        repetitions = (phase + count + 255) // 256
        self.position += count
        return (_RAMP * repetitions)[phase:phase + count]


def large_recording_spec() -> RecordingSpec:
    return RecordingSpec(
        RecordingIdentity(LARGE_RECORDING, 'ink-large'),
        ScreenSpec(LARGE_WIDTH, LARGE_HEIGHT, 10),
        NamedRegions({'boundary': LARGE_REGION}),
    )


def large_frames() -> Iterator[DeviceFrame]:
    yield DeviceFrame(FrameHeader(10), iter((
        DeviceBlock(Rect(0, 0, LARGE_WIDTH, LARGE_HEIGHT), PatternBody(LARGE_WIDTH, LARGE_HEIGHT)),
    )))
    yield DeviceFrame(FrameHeader(11), iter((
        DeviceBlock(Rect(127, 127, 130, 129), BytesIO(bytes((82, 83, 84, 85, 86, 87)))),
        DeviceBlock(Rect(128, 128, 131, 129), BytesIO(bytes((91, 92, 93)))),
    )))


def collect(db: ArchiveDatabase, recording_id: str, device: DeviceStream) -> list[int]:
    receipts = []
    while (frame := device.next_frame()) is not None:
        receipt = capture(db, recording_id, frame)
        receipts.append(receipt.frame_no)
    return receipts


def report_data(report: RegionReport) -> dict:
    return {
        'pixels': list(report.pixels),
        'origins': [[origin.frame_no, origin.block_index, origin.x, origin.y] for origin in report.origins],
        'gray_total': report.gray_total,
    }


def move_report_data(report) -> dict:
    return {
        'pixels': list(report.pixels),
        'origins': [[origin.frame_no, origin.block_index, origin.x, origin.y] for origin in report.origins],
        'moves': [
            None if move is None else [move.frame_no, move.command_index, move.x, move.y]
            for move in report.moves
        ],
        'gray_total': report.gray_total,
    }


def _morning_frames() -> Iterator[DeviceFrame]:
    yield DeviceFrame(FrameHeader(10), iter((DeviceBlock(Rect(0, 0, 2, 2), BytesIO(bytes((37, 2, 3, 4)))),)))
    yield DeviceFrame(FrameHeader(11), iter((
        DeviceBlock(Rect(0, 0, 1, 1), BytesIO(bytes((82,)))),
        DeviceBlock(Rect(0, 0, 1, 1), BytesIO(bytes((91,)))),
    )))


def _print_report(label: str, report: RegionReport) -> None:
    print(json.dumps({'stage': label, 'recording': report.recording_id, 'frame': report.frame_no, **report_data(report)}))


def main() -> None:
    recording_id = 'chamber-a'
    spec = RecordingSpec(
        RecordingIdentity(recording_id, 'ink-01'), ScreenSpec(2, 2, 10),
        NamedRegions({'reading': Rect(0, 0, 1, 2), 'alarm': Rect(1, 0, 2, 2)}),
    )
    with TemporaryDirectory(prefix='instrument-demo-') as folder:
        path = Path(folder) / 'archive.sqlite'
        with ArchiveDatabase(path) as db, DeviceStream(_morning_frames()) as device:
            register(db, spec)
            first = device.next_frame()
            if first is None:
                raise RuntimeError('demo source ended before its base frame')
            receipt = capture(db, recording_id, first)
            _print_report('morning-reading', inspect(db, recording_id, receipt.frame_no, 'reading'))
            _print_report('morning-alarm', inspect(db, recording_id, receipt.frame_no, 'alarm'))
            for published in collect(db, recording_id, device):
                _print_report('running-reading', inspect(db, recording_id, published, 'reading'))
        with ArchiveDatabase(path) as db:
            state = resume(db, recording_id)
            _print_report('device-off-history', inspect(db, recording_id, 11, 'reading'))
            print(json.dumps({'stage': 'resume', 'last_frame': state.last_frame, 'next_frame': state.next_frame}))
            block = DeviceBlock(Rect(1, 0, 2, 2), BytesIO(bytes((19, 20))))
            with DeviceStream((DeviceFrame(FrameHeader(12), iter((block,))),)) as device:
                published, = collect(db, recording_id, device)
        with ArchiveDatabase(path) as db:
            _print_report('previous-shift-alarm', inspect(db, recording_id, 11, 'alarm'))
            _print_report('new-shift-reading', inspect(db, recording_id, published, 'reading'))
            _print_report('new-shift-alarm', inspect(db, recording_id, published, 'alarm'))
        _move_stop_restart_demo(Path(folder))


def _move_stop_restart_demo(folder: Path) -> None:
    """Collect a rectangle move, stop the source, then reopen and claim it."""
    recording_id = 'chamber-move'
    screen = Rect(0, 0, 4, 1)
    spec = RecordingSpec(
        RecordingIdentity(recording_id, 'ink-move'), ScreenSpec(4, 1, 10),
        NamedRegions({'row': screen}),
    )
    path = folder / 'move-archive.sqlite'

    def frames() -> Iterator[DeviceFrame]:
        yield DeviceFrame(FrameHeader(10), iter((
            DeviceBlock(screen, BytesIO(bytes((1, 2, 3, 4)))),
        )))
        yield DeviceFrame(FrameHeader(11), iter((
            DeviceBlock(Rect(0, 0, 1, 1), BytesIO(bytes((9,)))),
            MoveRect(Rect(0, 0, 3, 1), Rect(1, 0, 4, 1)),
        )))

    with ArchiveDatabase(path) as db, DeviceStream(frames()) as device:
        register(db, spec)
        for frame_no in collect(db, recording_id, device):
            report = crop_with_moves(db, recording_id, frame_no, screen)
            print(json.dumps({'stage': 'move-collected', 'frame': frame_no,
                              **move_report_data(report)}))
    # The device source is now closed. A fresh connection reopens the archive.
    with ArchiveDatabase(path) as db:
        state = resume(db, recording_id)
        history = crop_with_moves(db, recording_id, 11, screen)
        print(json.dumps({'stage': 'move-reopen-resume', 'last_frame': state.last_frame,
                          'next_frame': state.next_frame}))
        print(json.dumps({'stage': 'move-reopen-fixed-report', 'frame': 11,
                          **move_report_data(history)}))
        legacy = crop_with_moves(db, recording_id, 10, screen)
        print(json.dumps({'stage': 'move-reopen-legacy-frame', 'frame': 10,
                          **move_report_data(legacy)}))


if __name__ == '__main__':
    main()
