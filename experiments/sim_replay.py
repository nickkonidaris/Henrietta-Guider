"""Simulator: replay every SUTR file in test/ through the production pipeline.

Inline orchestration (the real reducer module lives in Chunk 5; this
script does its job by hand) using only production modules:

  core.bpm.load_bpm                   master BPM (HDU 0; 1 = good)
  core.template.build_template        slope-fit -> Template
  core.framebuffer.FrameBuffer        rolling SUTR buffer + K-window diff
  core.sky.subtract_local_sky         per-row outer-1/6 sky
  core.xcor.xcor_2d                   sliced 2-D xcor + parabolic peak
  core.geometry.detector_to_sky       (dx_px, dy_px) -> (dRA, dDec)
  core.controller.Controller          per-axis P controller
  core.autoguider_server              fire-and-forget over socketpair
  core.wire.decode_command            peer-side decode (TCS view)

For each henNNNN_sssr.fits in `test/` (in (frame, sutr) order), the
simulator runs every step the live autoguider would run and records
the measurements. After replay, a multi-panel PNG is rendered showing
the time series.

Run: .venv/bin/python experiments/sim_replay.py
"""

from __future__ import annotations

import re
import socket
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from astropy.io import fits

from henrietta_guider.core.autoguider_server import AutoGuiderServer
from henrietta_guider.core.bpm import load_bpm
from henrietta_guider.core.controller import Controller, ControllerConfig
from henrietta_guider.core.framebuffer import FrameBuffer
from henrietta_guider.core.geometry import detector_to_sky
from henrietta_guider.core.sky import subtract_local_sky
from henrietta_guider.core.template import build_template
from henrietta_guider.core.types import Stamp
from henrietta_guider.core.wire import decode_command
from henrietta_guider.core.xcor import xcor_2d

REPO = Path(__file__).resolve().parent.parent
TEST_DIR = REPO / "test"
BPM_PATH = REPO / "bpm_25apr2026.fits"
OUT_PATH = REPO / "experiments" / "sim_replay.png"

SUTR_RE = re.compile(r"^hen(\d{4})_(\d{3})r\.fits$")

# Stamp + algorithm config (matches the demo and the spec defaults).
STAMP = Stamp(x_center=1024, x_halfwidth=25, y_lo=360, y_hi=1990)
PLATE_SCALE = 0.435
PA_DEG = 0.0
PARITY_X = +1
PARITY_Y = +1


@dataclass
class Sample:
    frame: int
    sutr: int
    dx_px: float | None
    dy_px: float | None
    dra_arcsec: float | None
    ddec_arcsec: float | None
    cmd_ra_arcsec: float | None
    cmd_dec_arcsec: float | None
    wire_decoded_ra: float | None
    wire_decoded_dec: float | None
    sent: bool


def find_sutrs(d: Path) -> list[tuple[int, int, Path]]:
    out: list[tuple[int, int, Path]] = []
    for p in sorted(d.iterdir()):
        m = SUTR_RE.match(p.name)
        if m:
            out.append((int(m.group(1)), int(m.group(2)), p))
    out.sort()
    return out


def main() -> int:
    print("Henrietta autoguider simulator — replaying test/ through the live pipeline\n")

    print(f"Loading BPM       {BPM_PATH.name} ...")
    good_full = load_bpm(BPM_PATH)
    print(f"  shape={good_full.shape}, good fraction={good_full.mean()*100:.2f}%")

    print(f"\nBuilding template hen1764.fits ...")
    template = build_template(TEST_DIR / "hen1764.fits", STAMP, good_full)
    print(f"  Template(frame={template.frame_number}, image.shape={template.image.shape})")

    fb = FrameBuffer(K=1, stride=1)
    cfg = ControllerConfig(Kp=0.5, Ki=0.0, Kd=0.0,
                           deadband_arcsec=0.025, max_command_arcsec=2.45)
    ctrl_ra = Controller(cfg)
    ctrl_dec = Controller(cfg)
    a, b = socket.socketpair()
    server = AutoGuiderServer.from_connected_socket(a, pacing_interval_s=0.0)

    good_stamp = good_full[STAMP.y_lo:STAMP.y_hi, STAMP.x_min:STAMP.x_max]

    sutrs = find_sutrs(TEST_DIR)
    print(f"\nReplaying {len(sutrs)} SUTR files...\n")
    print(f"  {'frame':>5} {'sutr':>4}  {'dx_px':>7} {'dy_px':>7}  "
          f"{'dRA\"':>8} {'dDec\"':>8}  {'cmd_RA\"':>8} {'cmd_Dec\"':>8}  "
          f"wire   ")

    history: list[Sample] = []
    for frame, sutr, path in sutrs:
        with fits.open(path) as hdul:
            raw = hdul[0].data.astype(np.float32)

        guide_full = fb.add(frame, sutr, raw)
        if guide_full is None:
            history.append(Sample(frame, sutr, None, None, None, None,
                                   None, None, None, None, False))
            print(f"  {frame:>5} {sutr:>4}   (warming up)")
            continue

        stamp_img = guide_full[STAMP.y_lo:STAMP.y_hi, STAMP.x_min:STAMP.x_max]
        sub, _ = subtract_local_sky(stamp_img, good_stamp)
        sub = np.where(good_stamp, sub, 0.0)

        xc = xcor_2d(sub, template.image, search=4)
        dra, ddec = detector_to_sky(
            xc.dx_px, xc.dy_px, PLATE_SCALE, PA_DEG, PARITY_X, PARITY_Y,
        )
        cmd_ra = ctrl_ra.step(dra)
        cmd_dec = ctrl_dec.step(ddec)
        sent = server.send_guide(cmd_ra, cmd_dec)
        wire_ra = wire_dec = None
        if sent:
            frame_bytes = b.recv(6)
            wire_ra, wire_dec = decode_command(frame_bytes)

        history.append(Sample(
            frame=frame, sutr=sutr,
            dx_px=xc.dx_px, dy_px=xc.dy_px,
            dra_arcsec=dra, ddec_arcsec=ddec,
            cmd_ra_arcsec=cmd_ra, cmd_dec_arcsec=cmd_dec,
            wire_decoded_ra=wire_ra, wire_decoded_dec=wire_dec,
            sent=sent,
        ))
        wire_str = f"{wire_ra:+.2f},{wire_dec:+.2f}" if sent else "(deadband)"
        print(f"  {frame:>5} {sutr:>4}  {xc.dx_px:+7.3f} {xc.dy_px:+7.3f}  "
              f"{dra:+8.3f} {ddec:+8.3f}  "
              f"{cmd_ra:+8.3f} {cmd_dec:+8.3f}  {wire_str}")

    a.close()
    b.close()

    print("\nRendering PNG...")
    render(history)
    print(f"  saved to {OUT_PATH}")

    # Summary.
    n_total = len(history)
    n_warmup = sum(1 for h in history if h.dx_px is None)
    n_sent = sum(1 for h in history if h.sent)
    n_deadband = n_total - n_warmup - n_sent
    print(f"\nSummary:")
    print(f"  {n_total} SUTRs processed")
    print(f"  {n_warmup}  warmup (no guide image yet)")
    print(f"  {n_sent}  G-frames sent on wire")
    print(f"  {n_deadband}  in dead-band (no command needed)")
    return 0


def render(history: list[Sample]) -> None:
    fig, axes = plt.subplots(4, 1, figsize=(13.5, 9.0), sharex=True,
                             facecolor="#ECECEC")
    idx = np.arange(len(history))
    frames = np.array([h.frame for h in history])

    def trace(ax, attr, color, label):
        y = np.array([getattr(h, attr) if getattr(h, attr) is not None else np.nan
                       for h in history])
        ax.plot(idx, y, "o-", color=color, ms=4, lw=1.0, label=label)

    # 1) detector-frame measured drift.
    ax = axes[0]
    trace(ax, "dx_px", "#1f77b4", "dx_px")
    trace(ax, "dy_px", "#d62728", "dy_px")
    ax.axhline(0, color="#888", lw=0.5, ls=":")
    ax.set_ylabel("xcor (px)", fontsize=9)
    ax.legend(loc="upper right", fontsize=8)
    ax.set_title("Henrietta autoguider — full pipeline replay on real test/ frames",
                 fontsize=11, loc="left", fontweight="bold")

    # 2) sky-frame correction.
    ax = axes[1]
    trace(ax, "dra_arcsec", "#1f77b4", "dRA")
    trace(ax, "ddec_arcsec", "#d62728", "dDec")
    ax.axhline(0, color="#888", lw=0.5, ls=":")
    ax.set_ylabel("sky correction (arcsec)", fontsize=9)
    ax.legend(loc="upper right", fontsize=8)

    # 3) controller commands.
    ax = axes[2]
    trace(ax, "cmd_ra_arcsec", "#1f77b4", "cmd RA")
    trace(ax, "cmd_dec_arcsec", "#d62728", "cmd Dec")
    ax.axhline(0, color="#888", lw=0.5, ls=":")
    ax.set_ylabel("controller cmd (arcsec)", fontsize=9)
    ax.legend(loc="upper right", fontsize=8)

    # 4) wire-decoded values (what the TCS would actually receive).
    ax = axes[3]
    trace(ax, "wire_decoded_ra", "#1f77b4", "wire RA")
    trace(ax, "wire_decoded_dec", "#d62728", "wire Dec")
    ax.axhline(0, color="#888", lw=0.5, ls=":")
    ax.set_ylabel("on-wire (arcsec)", fontsize=9)
    ax.set_xlabel("SUTR index across all frames", fontsize=9)
    ax.legend(loc="upper right", fontsize=8)

    # Frame boundaries: vertical lines + frame-number labels.
    boundaries = [i for i in range(1, len(frames)) if frames[i] != frames[i - 1]]
    for ax in axes:
        for x in boundaries:
            ax.axvline(x - 0.5, color="#3aa55d", lw=0.6, alpha=0.5)
    # Annotate the topmost axes with frame numbers at midpoint of each run.
    boundaries_full = [0] + boundaries + [len(frames)]
    for lo, hi in zip(boundaries_full[:-1], boundaries_full[1:]):
        mid = (lo + hi - 1) / 2.0
        axes[0].annotate(
            f"hen{frames[lo]:04d}",
            xy=(mid, axes[0].get_ylim()[1]),
            xytext=(mid, axes[0].get_ylim()[1] * 1.05),
            ha="center", va="bottom", fontsize=8, color="#3aa55d",
            annotation_clip=False,
        )

    fig.tight_layout()
    fig.savefig(OUT_PATH, dpi=130)


if __name__ == "__main__":
    raise SystemExit(main())
