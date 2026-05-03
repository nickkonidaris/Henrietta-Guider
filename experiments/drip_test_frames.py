"""Continuously feed test/ frames to a watched directory.

Cycles through the test/ frames (hen1764, hen1765, …), copies each
slope file + its SUTRs into --watch-dir, and increments the frame
number on every cycle so the watcher sees fresh frames. Runs forever
until Ctrl-C.

Usage:
    uv run python experiments/drip_test_frames.py --watch-dir /tmp/hen-smoke

Useful args:
    --start-frame 2000        target frame numbers begin here
    --inter-sutr-s 0.5        delay between SUTRs in a frame
    --inter-frame-s 2.0       delay between frames
    --source-frame 1764       only replay this source frame (default: cycle all)
    --max-frames 50           stop after N target frames (default: forever)

The watcher in henrietta-tui / henrietta-cli matches `henNNNN.fits`
(slope) and `henNNNN_sssr.fits` (SUTR), so we keep that exact naming
for the targets — only the frame number differs from the source.
"""

from __future__ import annotations

import argparse
import re
import shutil
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEST_DIR = REPO / "test"

_SLOPE_RE = re.compile(r"^hen(\d{4})\.fits$")
_SUTR_RE = re.compile(r"^hen(\d{4})_(\d{3})r\.fits$")


def _group_test_frames(test_dir: Path) -> dict[int, dict]:
    """Group test/ FITS files by source frame number.

    Returns {frame_number: {"slope": Path | None, "sutrs": [(sutr_n, Path), ...]}}.
    """
    by_frame: dict[int, dict] = {}
    for p in sorted(test_dir.iterdir()):
        m_sutr = _SUTR_RE.match(p.name)
        if m_sutr:
            frame = int(m_sutr.group(1))
            sutr_n = int(m_sutr.group(2))
            by_frame.setdefault(frame, {"slope": None, "sutrs": []})
            by_frame[frame]["sutrs"].append((sutr_n, p))
            continue
        m_slope = _SLOPE_RE.match(p.name)
        if m_slope:
            frame = int(m_slope.group(1))
            by_frame.setdefault(frame, {"slope": None, "sutrs": []})
            by_frame[frame]["slope"] = p
    for group in by_frame.values():
        group["sutrs"].sort()
    return by_frame


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="drip_test_frames")
    parser.add_argument("--watch-dir", required=True, type=Path)
    parser.add_argument("--start-frame", type=int, default=2000, help="first target frame number")
    parser.add_argument(
        "--inter-sutr-s",
        type=float,
        default=0.5,
        help="seconds between SUTRs within a frame",
    )
    parser.add_argument(
        "--inter-frame-s",
        type=float,
        default=2.0,
        help="seconds between successive frames",
    )
    parser.add_argument(
        "--source-frame",
        type=int,
        default=None,
        help="only replay this one source frame from test/ (default: cycle through all available)",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=0,
        help="stop after N target frames (0 = forever)",
    )
    args = parser.parse_args(argv)

    args.watch_dir.mkdir(parents=True, exist_ok=True)

    by_frame = _group_test_frames(TEST_DIR)
    if not by_frame:
        parser.error(f"no FITS files found in {TEST_DIR}")

    if args.source_frame is not None:
        if args.source_frame not in by_frame:
            parser.error(
                f"source frame {args.source_frame} not in {TEST_DIR}; available: {sorted(by_frame)}"
            )
        sources = [args.source_frame]
    else:
        sources = sorted(by_frame)

    print(f"Drip into : {args.watch_dir}")
    print(f"Sources   : hen{sources!r}")
    print(f"Target    : starting at frame {args.start_frame:04d}")
    print(f"Cadence   : {args.inter_sutr_s}s/SUTR, {args.inter_frame_s}s/frame")
    print()

    target = args.start_frame
    delivered = 0
    try:
        while True:
            for src in sources:
                group = by_frame[src]
                slope = group["slope"]
                if slope is not None:
                    target_path = args.watch_dir / f"hen{target:04d}.fits"
                    shutil.copy(slope, target_path)
                    print(f"  hen{target:04d}.fits        <- {slope.name}")
                    # Brief pause so the watcher's settle timer fires before
                    # the first SUTR enqueues.
                    time.sleep(max(args.inter_sutr_s, 0.4))
                for sutr_n, sutr_path in group["sutrs"]:
                    target_path = args.watch_dir / f"hen{target:04d}_{sutr_n:03d}r.fits"
                    shutil.copy(sutr_path, target_path)
                    print(f"  hen{target:04d}_{sutr_n:03d}r.fits  <- {sutr_path.name}")
                    time.sleep(args.inter_sutr_s)
                target += 1
                delivered += 1
                if args.max_frames and delivered >= args.max_frames:
                    print(f"\nDelivered {delivered} frames. Stopping.")
                    return 0
                time.sleep(args.inter_frame_s)
    except KeyboardInterrupt:
        print(f"\nStopped after {delivered} frame(s).")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
