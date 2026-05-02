"""Headless CLI entry point: `henrietta-cli`.

Loads config, starts the autoguider runtime (TCP listener + Worker),
and runs until SIGINT.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from pathlib import Path

from henrietta_guider.core.bpm import load_bpm
from henrietta_guider.core.config import load_config
from henrietta_guider.core.types import Stamp
from henrietta_guider.runtime import run_autoguider


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="henrietta-cli")
    parser.add_argument("--config", default="~/.config/henrietta_guider/config.toml")
    parser.add_argument("--watch-dir", required=True)
    parser.add_argument("--bpm", default=None, help="Override files.bad_pixel_mask")
    args = parser.parse_args(argv)

    if not Path(args.watch_dir).expanduser().is_dir():
        parser.error(f"--watch-dir does not exist: {args.watch_dir}")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = load_config(args.config)

    bpm_path = Path(args.bpm or cfg.files.bad_pixel_mask).expanduser()
    bpm_good = load_bpm(bpm_path)

    sci_stamp = Stamp(
        # placeholder; real value from session
        x_center=cfg.detector.y_middle_row,
        x_halfwidth=cfg.reduction.stamp_x_halfwidth_px,
        y_lo=cfg.reduction.stamp_y_lo,
        y_hi=cfg.reduction.stamp_y_hi,
    )

    log = logging.getLogger(__name__)

    stop = False

    def handle_sigint(signum, frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, handle_sigint)

    with run_autoguider(
        cfg=cfg,
        watch_dir=args.watch_dir,
        science_stamp=sci_stamp,
        bpm_good=bpm_good,
        on_status=lambda s: log.info("server: %s", s),
    ):
        while not stop:
            signal.pause()  # blocks until SIGINT
    return 0


if __name__ == "__main__":
    sys.exit(main())
