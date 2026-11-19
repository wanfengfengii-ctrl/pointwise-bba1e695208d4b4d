"""Joint verification for rectangle moves beyond the unittest suite.

These checks exercise the end-to-end contracts from MOVES.md that are awkward
to express as isolated unit tests: the documented single-pass example, the
8192x8192 large-screen move with the 16 MiB peak-RSS ceiling (measured in a
fresh process, like the original large regression), and stop/restart claiming.
Failures raise, so a non-zero exit code propagates to the runtime ``verify``.
"""
from __future__ import annotations

import json
import resource
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parent
MAX_EXTRA_RSS = 16 * 1024 * 1024

_LARGE_SCRIPT = r'''
import json, resource, sys
from pathlib import Path
from archive import ArchiveDatabase, resume
from instrument_demo import (
    LARGE_RECORDING, LARGE_REGION, collect, large_frames, large_recording_spec,
)
from inspection import crop_with_moves
from intake import DeviceStream, MoveRect
from move_examples import large_move_frame
from registration import register


def peak_rss():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == "darwin" else value * 1024


phase, path = sys.argv[1], sys.argv[2]
idle = peak_rss()
with ArchiveDatabase(path) as db:
    if phase == "capture":
        register(db, large_recording_spec())
        with DeviceStream(large_frames()) as device:
            first = collect(db, LARGE_RECORDING, device)
        with DeviceStream((large_move_frame(MoveRect),)) as device:
            second = collect(db, LARGE_RECORDING, device)
        result = {"published": first + second}
    else:
        report = crop_with_moves(db, LARGE_RECORDING, 12, LARGE_REGION)
        result = {
            "pixels": list(report.pixels),
            "gray_total": report.gray_total,
            "origins": [[o.frame_no, o.block_index, o.x, o.y] for o in report.origins],
            "moves": [None if m is None else [m.frame_no, m.command_index, m.x, m.y]
                      for m in report.moves],
            "next_frame": resume(db, LARGE_RECORDING).next_frame,
        }
result["memory"] = {"idle_rss": idle, "peak_rss": peak_rss()}
result["memory"]["extra_rss"] = result["memory"]["peak_rss"] - idle
print(json.dumps(result))
'''


def _run_worker(phase: str, path: Path) -> dict:
    result = subprocess.run(
        [sys.executable, "-B", "-c", _LARGE_SCRIPT, phase, str(path)],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"large {phase} worker failed:\n{result.stdout}\n{result.stderr}")
    return json.loads(result.stdout)


def verify_large_move() -> None:
    with TemporaryDirectory(prefix="instrument-large-move-") as folder:
        path = Path(folder) / "archive.sqlite"
        captured = _run_worker("capture", path)
        reopened = _run_worker("read", path)

    if captured["published"] != [10, 11, 12]:
        raise AssertionError(f"unexpected published frames: {captured['published']}")
    for label, measurement in (("capture", captured["memory"]), ("read", reopened["memory"])):
        if measurement["extra_rss"] > MAX_EXTRA_RSS:
            raise AssertionError(f"{label} extra RSS {measurement['extra_rss']} exceeds {MAX_EXTRA_RSS}")

    expected_pixels = [247, 248, 249, 250, 251, 252, 250, 251, 82, 83, 84, 255]
    expected_origins = [
        [10, 0, 125, 126], [10, 0, 126, 126], [10, 0, 127, 126],
        [10, 0, 128, 126], [10, 0, 129, 126], [10, 0, 130, 126],
        [10, 0, 125, 127], [10, 0, 126, 127], [11, 0, 0, 0],
        [11, 0, 1, 0], [11, 0, 2, 0], [10, 0, 130, 127],
    ]
    expected_moves = [[12, 0, x - 1, y - 1]
                      for y in range(127, 129) for x in range(126, 132)]
    if reopened["pixels"] != expected_pixels:
        raise AssertionError(f"large pixels mismatch: {reopened['pixels']}")
    if reopened["origins"] != expected_origins:
        raise AssertionError(f"large origins mismatch: {reopened['origins']}")
    if reopened["moves"] != expected_moves:
        raise AssertionError(f"large moves mismatch: {reopened['moves']}")
    if reopened["gray_total"] != 2502 or reopened["next_frame"] != 13:
        raise AssertionError(f"large total/resume mismatch: {reopened}")
    print("MOVE-VERIFY large-screen " + json.dumps({
        "capture_extra_rss": captured["memory"]["extra_rss"],
        "read_extra_rss": reopened["memory"]["extra_rss"],
        "gray_total": reopened["gray_total"],
    }))


def main() -> int:
    verify_large_move()
    print("MOVE-VERIFY result=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
