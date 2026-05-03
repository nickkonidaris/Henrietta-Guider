"""Visual: show what region of the frame the xcor actually consumes.

Three panels:
  1. Full hen1764.fits with the science stamp drawn as a rectangle.
  2. Just the stamp itself (cropped, sky-subtracted), with sky bands shaded.
  3. A blow-up of a small section so the trace's narrow spatial profile is visible.

Plus, two side experiments demonstrating why a small 51×51 stamp would fail
on a spectrograph:
  4. xcor recovery error vs Y-extent of the stamp (truth-test sweep).

Output: experiments/show_what_xcor_sees.png

Run: .venv/bin/python experiments/show_what_xcor_sees.py
"""

from __future__ import annotations

import os
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
from astropy.io import fits

REPO = Path(__file__).resolve().parent.parent
TEST_DIR = REPO / "test"
OUT_PATH = REPO / "experiments" / "show_what_xcor_sees.png"

X_CENTER = 1024
X_HALFWIDTH = 25
Y_LO = 360
Y_HI = 1990
INJECTED_DY = -5


def subtract_local_sky(stamp: np.ndarray) -> np.ndarray:
    nx = stamp.shape[1]
    edge = max(1, nx // 6)
    sky = np.median(
        np.concatenate([stamp[:, :edge], stamp[:, -edge:]], axis=1),
        axis=1,
    )
    return stamp - sky[:, None]


def xcor_2d(data: np.ndarray, template: np.ndarray, search: int = 12) -> np.ndarray:
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
    return ix - search, iy - search, C


def main() -> int:
    print("Loading hen1764.fits...")
    with fits.open(TEST_DIR / "hen1764.fits") as hdul:
        full = hdul[0].data.astype(np.float32)

    stamp_full = full[Y_LO:Y_HI, X_CENTER - X_HALFWIDTH : X_CENTER + X_HALFWIDTH + 1]
    stamp_subbed = subtract_local_sky(stamp_full)

    # Side experiment: Y-extent sweep. For each Y-extent, run a truth
    # test (roll by -5) and see whether dy is recovered.
    print("Sweeping Y-extent for truth-recovery test...")
    y_extents = [50, 100, 200, 400, 800, 1200, Y_HI - Y_LO]
    recovered_dy = []
    for y_ext in y_extents:
        sl = stamp_subbed[:y_ext]
        rolled = np.roll(sl, INJECTED_DY, axis=0)
        dx, dy, _ = xcor_2d(rolled, sl, search=12)
        recovered_dy.append(dy)
        print(f"  Y-extent {y_ext:>4d} px → recovered dy = {dy:+.2f} px "
              f"(injected {INJECTED_DY})")

    print("Rendering...")
    fig = plt.figure(figsize=(13.5, 9.5), facecolor="#ECECEC")
    gs = fig.add_gridspec(
        nrows=2, ncols=3,
        height_ratios=[5.0, 3.0],
        width_ratios=[2.0, 1.0, 1.4],
        hspace=0.35, wspace=0.30,
        left=0.05, right=0.97, top=0.93, bottom=0.07,
    )

    # Top-left: full frame with stamp rectangle.
    ax_full = fig.add_subplot(gs[0, 0])
    vlo, vhi = np.percentile(full, [2, 99.5])
    ax_full.imshow(full, origin="lower", cmap="viridis", aspect="auto",
                   vmin=vlo, vmax=vhi, interpolation="nearest")
    rect = mpatches.Rectangle(
        (X_CENTER - X_HALFWIDTH, Y_LO),
        2 * X_HALFWIDTH + 1, Y_HI - Y_LO,
        ec="#E63946", fc="none", lw=2.0,
    )
    ax_full.add_patch(rect)
    ax_full.set_title(
        f"Full hen1764.fits (2048×2048)  +  science stamp "
        f"(X=[{X_CENTER-X_HALFWIDTH},{X_CENTER+X_HALFWIDTH+1}), "
        f"Y=[{Y_LO},{Y_HI}))  =  51 × {Y_HI-Y_LO} px (1.9% of frame)",
        fontsize=10, color="#222",
    )
    ax_full.set_xlabel("X (px)"); ax_full.set_ylabel("Y (px)")

    # Top-middle: stamp itself.
    ax_stamp = fig.add_subplot(gs[0, 1])
    sx, sy = stamp_subbed.shape[1], stamp_subbed.shape[0]
    vlo_s, vhi_s = np.percentile(stamp_subbed, [2, 99.5])
    ax_stamp.imshow(stamp_subbed, origin="lower", cmap="viridis",
                    aspect="auto", vmin=vlo_s, vmax=vhi_s,
                    interpolation="nearest")
    edge = max(1, sx // 6)
    ax_stamp.add_patch(mpatches.Rectangle(
        (-0.5, -0.5), edge, sy, ec="none", fc="#E63946", alpha=0.18,
    ))
    ax_stamp.add_patch(mpatches.Rectangle(
        (sx - edge - 0.5, -0.5), edge, sy, ec="none", fc="#E63946", alpha=0.18,
    ))
    ax_stamp.text(edge / 2 - 0.5, sy * 1.005, "sky", color="#E63946",
                  fontsize=8, ha="center", va="bottom")
    ax_stamp.text(sx - edge / 2 - 0.5, sy * 1.005, "sky",
                  color="#E63946", fontsize=8, ha="center", va="bottom")
    ax_stamp.set_title(
        f"The stamp (sky-subtracted)\n"
        f"shape ({sy}, {sx}); 1/6 sky bands shaded",
        fontsize=10,
    )
    ax_stamp.set_xlabel("local X (px)"); ax_stamp.set_ylabel("local Y (px)")

    # Top-right: zoom into the trace cross-section.
    ax_zoom = fig.add_subplot(gs[0, 2])
    # Pick a Y row near the middle.
    y_mid = sy // 2
    y_zoom_lo = max(0, y_mid - 25)
    y_zoom_hi = min(sy, y_mid + 25)
    ax_zoom.imshow(
        stamp_subbed[y_zoom_lo:y_zoom_hi, :],
        origin="lower", cmap="viridis", aspect="auto",
        vmin=vlo_s, vmax=vhi_s, interpolation="nearest",
    )
    ax_zoom.set_title(
        f"Zoom: rows [{y_zoom_lo}, {y_zoom_hi})\n"
        f"the trace is the bright ~5 px column",
        fontsize=10,
    )
    ax_zoom.set_xlabel("local X (px)")

    # Bottom: Y-extent vs recovery error.
    ax_sweep = fig.add_subplot(gs[1, :])
    errors = [abs(rec - INJECTED_DY) for rec in recovered_dy]
    ax_sweep.semilogy(y_extents, errors, "o-", color="#1f6feb", lw=2,
                      ms=8, label="|recovered dy - injected dy|")
    for x, e, dy_rec in zip(y_extents, errors, recovered_dy):
        ax_sweep.annotate(
            f"  rec dy={dy_rec:+.2f}",
            (x, e), fontsize=8, color="#444", va="bottom",
        )
    ax_sweep.axhline(0.1, color="#3aa55d", ls=":", alpha=0.7,
                     label="0.1 px (acceptable)")
    ax_sweep.axhline(1.0, color="#E63946", ls=":", alpha=0.7,
                     label="1.0 px (bad)")
    ax_sweep.set_xlabel("Stamp Y-extent (px)")
    ax_sweep.set_ylabel("|dy_recovered − dy_injected|  (px)")
    ax_sweep.set_title(
        "Y-recovery error vs stamp Y-extent  "
        f"(truth test: data = roll(template, dx=0, dy={INJECTED_DY}))",
        fontsize=11, color="#222",
    )
    ax_sweep.legend(loc="upper right", fontsize=9)
    ax_sweep.grid(True, alpha=0.3, which="both")

    fig.suptitle(
        "What region of the detector does the 2-D xcor actually consume?",
        fontsize=12, fontweight="bold", x=0.05, y=0.97, ha="left",
    )
    fig.savefig(OUT_PATH, dpi=130)
    print(f"  saved to {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
