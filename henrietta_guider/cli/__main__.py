"""Headless CLI entry point: `henrietta-cli`.

Loads config, opens a TCP listener, waits for the TCS to connect, builds
the worker, and runs until SIGINT.
"""

from __future__ import annotations

import argparse
import logging
import signal
import socket
import sys
from pathlib import Path

from henrietta_guider.core.bpm import load_bpm
from henrietta_guider.core.config import load_config
from henrietta_guider.core.types import Stamp
from henrietta_guider.core.worker import Worker


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

    # Autoguider acts as TCP server; TCS connects to us. Bind & accept
    # the first incoming connection (full re-listen on disconnect lives
    # in worker.py — Chunk 6 / 6.4).
    log = logging.getLogger(__name__)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((cfg.tcs.bind_host, cfg.tcs.listen_port))
        listener.listen(1)
        log.info(
            "listening for TCS on %s:%d",
            cfg.tcs.bind_host,
            cfg.tcs.listen_port,
        )
        sock, peer = listener.accept()
    log.info("TCS connected from %s", peer)

    stop = False

    def handle_sigint(signum, frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, handle_sigint)

    with Worker.run(
        cfg=cfg,
        watch_dir=args.watch_dir,
        science_stamp=sci_stamp,
        bpm_good=bpm_good,
        tcs_socket=sock,
    ):
        while not stop:
            signal.pause()  # blocks until SIGINT
    return 0


if __name__ == "__main__":
    sys.exit(main())
