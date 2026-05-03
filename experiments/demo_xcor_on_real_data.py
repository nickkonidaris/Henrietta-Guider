"""Standalone demonstration: run the ALGORITHM.md 2-D xcor on real frames.

Loads two consecutive slope-fit frames (hen1764.fits and hen1765.fits)
from `test/`, applies the master BPM, sky-subtracts, and runs the
brute-force 2-D cross-correlation + parabolic sub-pixel peak fit.

Two experiments:
  1. Truth test: roll hen1764 by a known (dx_inj, dy_inj), feed the
     rolled copy as data with the original as template, recover the
     shift. Confirms the algorithm itself works.
  2. Real test: hen1764 as template, hen1765 as data. Reports the
     measured shift between the two consecutive integrations.

Output: a 6-panel PNG at experiments/demo_xcor_on_real_data.png plus
text printed to stdout.

Run:
    .venv/bin/python experiments/demo_xcor_on_real_data.py
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from astropy.io import fits

REPO = Path(__file__).resolve().parent.parent
TEST_DIR = REPO / "test"
BPM_PATH = REPO / "bpm_25apr2026.fits"
OUT_PATH = REPO / "experiments" / "demo_xcor_on_real_data.png"

# Stamp parameters (from peeking at hen1764.fits):
# The trace is illuminated Y=350..1994 in this frame; use the full
# extent so the filter cutoffs (which carry most of the Y-direction
# information) are inside the stamp.
X_CENTER = 1024
X_HALFWIDTH = 25
Y_LO = 360
Y_HI = 1990
SEARCH = 12

INJECTED_DX = 3
INJECTED_DY = -5


def load_master_bpm(path: Path) -> np.ndarray:
    """HDU 0 master mask: 1 = good. Returns boolean."""
    with fits.open(path) as hdul:
        return hdul[0].data.astype(bool)


def load_frame_float(path: Path) -> np.ndarray:
    with fits.open(path) as hdul:
        return hdul[0].data.astype(np.float32)


def extract_stamp(full: np.ndarray, x_c: int, halfw: int,
                  y_lo: int, y_hi: int) -> np.ndarray:
    """ALGORITHM.md uses [x_c - halfw : x_c + halfw + 1]."""
    return full[y_lo:y_hi, x_c - halfw : x_c + halfw + 1].copy()


def subtract_local_sky(stamp: np.ndarray, good: np.ndarray
                       ) -> tuple[np.ndarray, np.ndarray]:
    """Per-row median of outer 1/6 columns on each side; mask-aware."""
    ny, nx = stamp.shape
    edge = max(1, nx // 6)
    edge_cols = np.zeros(nx, dtype=bool)
    edge_cols[:edge] = True
    edge_cols[-edge:] = True
    masked = np.where(good & edge_cols[None, :], stamp, np.nan)
    per_row = np.nanmedian(masked, axis=1).astype(stamp.dtype)
    return stamp - per_row[:, None], per_row


def xcor_2d(data: np.ndarray, template: np.ndarray, search: int
            ) -> tuple[float, float, np.ndarray]:
    """Brute-force 2-D cross-correlation over ±search; returns
    (dx_sub, dy_sub, correlation_surface). Sub-pixel peak via parabolic
    fit in each axis independently.
    """
    ny, nx = template.shape
    n = 2 * search + 1
    C = np.zeros((n, n), dtype=np.float64)
    for iy, dy in enumerate(range(-search, search + 1)):
        for ix, dx in enumerate(range(-search, search + 1)):
            y_lo_t = max(0, -dy)
            y_hi_t = ny - max(0, dy)
            x_lo_t = max(0, -dx)
            x_hi_t = nx - max(0, dx)
            t = template[y_lo_t:y_hi_t, x_lo_t:x_hi_t]
            d = data[y_lo_t + dy : y_hi_t + dy, x_lo_t + dx : x_hi_t + dx]
            C[iy, ix] = float(np.sum(t * d))

    iy, ix = np.unravel_index(int(np.argmax(C)), C.shape)
    sub_x = _parabolic(C[iy, ix - 1], C[iy, ix], C[iy, ix + 1]) if 0 < ix < n - 1 else 0.0
    sub_y = _parabolic(C[iy - 1, ix], C[iy, ix], C[iy + 1, ix]) if 0 < iy < n - 1 else 0.0
    return (ix - search) + sub_x, (iy - search) + sub_y, C


def _parabolic(a: float, b: float, c: float) -> float:
    denom = a - 2.0 * b + c
    if denom == 0.0:
        return 0.0
    return 0.5 * (a - c) / denom


def measure(data_full: np.ndarray, template_full: np.ndarray,
            bpm_good: np.ndarray) -> dict:
    """Run the full pipeline on two full-detector images, return a dict."""
    sci_t = extract_stamp(template_full, X_CENTER, X_HALFWIDTH, Y_LO, Y_HI)
    sci_d = extract_stamp(data_full,     X_CENTER, X_HALFWIDTH, Y_LO, Y_HI)
    good  = extract_stamp(bpm_good,      X_CENTER, X_HALFWIDTH, Y_LO, Y_HI)

    t_sub, _t_sky = subtract_local_sky(sci_t, good)
    d_sub, _d_sky = subtract_local_sky(sci_d, good)

    # Mask out bad pixels by zeroing them so they don't contribute.
    t_sub_m = np.where(good, t_sub, 0.0)
    d_sub_m = np.where(good, d_sub, 0.0)

    t0 = time.monotonic()
    dx, dy, C = xcor_2d(d_sub_m, t_sub_m, search=SEARCH)
    elapsed_ms = (time.monotonic() - t0) * 1000

    return {
        "template_stamp_subbed": t_sub_m,
        "data_stamp_subbed":     d_sub_m,
        "correlation":           C,
        "dx_px":                 dx,
        "dy_px":                 dy,
        "xcor_ms":               elapsed_ms,
        "good":                  good,
    }


def render(truth_result: dict, real_result: dict) -> None:
    fig, axes = plt.subplots(2, 3, figsize=(13.5, 8.5),
                             facecolor="#ECECEC")

    def show_stamp(ax, img, title):
        # Use a sensible stretch — the trace is bright, sky is near zero.
        vmax = np.percentile(img, 99.5)
        vmin = np.percentile(img, 1)
        ax.imshow(img, origin="lower", aspect="auto", cmap="viridis",
                  vmin=vmin, vmax=vmax, interpolation="nearest")
        ax.set_title(title, fontsize=9)
        ax.tick_params(labelsize=7)

    def show_corr(ax, C, dx, dy, title):
        ax.imshow(C, origin="lower", cmap="magma", aspect="auto",
                  interpolation="nearest",
                  extent=[-SEARCH, SEARCH, -SEARCH, SEARCH])
        ax.scatter([dx], [dy], s=80, c="#3aa55d", marker="x",
                   linewidths=2, label=f"peak: ({dx:+.2f}, {dy:+.2f})")
        ax.legend(loc="upper right", fontsize=8, framealpha=0.85)
        ax.set_xlabel("dx (px)"); ax.set_ylabel("dy (px)")
        ax.set_title(title, fontsize=9)
        ax.tick_params(labelsize=7)

    # Row 1: truth test.
    show_stamp(axes[0, 0], truth_result["template_stamp_subbed"],
               "Truth: template (hen1764, sky-subbed)")
    show_stamp(axes[0, 1], truth_result["data_stamp_subbed"],
               f"Truth: data = roll(template, dx={INJECTED_DX}, dy={INJECTED_DY})")
    show_corr(axes[0, 2], truth_result["correlation"],
              truth_result["dx_px"], truth_result["dy_px"],
              "Truth: correlation surface")

    # Row 2: real test.
    show_stamp(axes[1, 0], real_result["template_stamp_subbed"],
               "Real: template (hen1764, sky-subbed)")
    show_stamp(axes[1, 1], real_result["data_stamp_subbed"],
               "Real: data (hen1765, sky-subbed)")
    show_corr(axes[1, 2], real_result["correlation"],
              real_result["dx_px"], real_result["dy_px"],
              "Real: correlation surface")

    fig.suptitle(
        "ALGORITHM.md 2-D xcor on real Henrietta frames "
        f"(stamp X={X_CENTER}±{X_HALFWIDTH}, Y={Y_LO}..{Y_HI})",
        fontsize=11, fontweight="bold", x=0.04, y=0.98, ha="left",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(OUT_PATH, dpi=130)


def main() -> int:
    print("Loading frames...")
    t0 = time.monotonic()
    bpm_good = load_master_bpm(BPM_PATH)
    template = load_frame_float(TEST_DIR / "hen1764.fits")
    data     = load_frame_float(TEST_DIR / "hen1765.fits")
    print(f"  loaded BPM, hen1764, hen1765 in {(time.monotonic() - t0)*1000:.0f} ms")
    print(f"  BPM good fraction: {bpm_good.mean()*100:.2f}%")
    print(f"  hen1764 range: {template.min():.0f}..{template.max():.0f}")
    print(f"  hen1765 range: {data.min():.0f}..{data.max():.0f}")

    # 1. Truth test: roll the template by a known shift.
    rolled = np.roll(np.roll(template, INJECTED_DY, axis=0),
                     INJECTED_DX, axis=1)
    print(f"\nTruth test: data = roll(template, dx={INJECTED_DX}, "
          f"dy={INJECTED_DY})")
    truth = measure(rolled, template, bpm_good)
    print(f"  recovered: dx={truth['dx_px']:+.3f} px, "
          f"dy={truth['dy_px']:+.3f} px")
    print(f"  xcor took {truth['xcor_ms']:.0f} ms over a "
          f"({2*SEARCH+1}, {2*SEARCH+1}) search grid")
    print(f"  truth error: dx={truth['dx_px'] - INJECTED_DX:+.3f} px, "
          f"dy={truth['dy_px'] - INJECTED_DY:+.3f} px "
          f"(magnitude {np.hypot(truth['dx_px']-INJECTED_DX, truth['dy_px']-INJECTED_DY):.3f} px)")

    # 2. Real test: hen1764 vs hen1765.
    print("\nReal test: hen1764 (template) vs hen1765 (data)")
    real = measure(data, template, bpm_good)
    print(f"  recovered: dx={real['dx_px']:+.3f} px, "
          f"dy={real['dy_px']:+.3f} px")
    print(f"  xcor took {real['xcor_ms']:.0f} ms")

    # 3. Render the PNG.
    print(f"\nRendering {OUT_PATH}...")
    render(truth, real)
    print(f"  done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
