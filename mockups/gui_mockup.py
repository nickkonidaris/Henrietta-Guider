"""Static mockup of the Henrietta autoguider GUI.

Renders the planned operator-window layout as a single matplotlib figure
populated with synthetic data, so we can iterate on layout / colour /
panel placement before any real GUI code is written.

Run:
    python3 mockups/gui_mockup.py            # writes mockups/gui_mockup.png

Not part of the production app. Pure documentation artifact.
"""

from __future__ import annotations

import os

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.gridspec import GridSpec

OUT_PATH = os.path.join(os.path.dirname(__file__), "gui_mockup.png")


# ---------------------------------------------------------------------------
# Synthetic data
# ---------------------------------------------------------------------------

def synth_image(
    nx: int = 220,
    ny: int = 1024,
    ridge_x_center: float = 110.0,
    ridge_angle_deg: float = 1.6,
    fwhm_px: float = 3.5,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """Build a fake guide-image: a tilted near-vertical trace with a couple
    of absorption features, plus sky and noise."""
    if rng is None:
        rng = np.random.default_rng(7)
    y_full = np.arange(ny) - ny // 2  # zero-centred at detector middle row
    x_grid = np.arange(nx)[None, :]
    y_grid = np.arange(ny)[:, None]

    angle_rad = np.deg2rad(ridge_angle_deg)
    x_ridge = ridge_x_center + np.tan(angle_rad) * y_full

    sigma = fwhm_px / 2.355
    profile = np.exp(-((x_grid - x_ridge[:, None]) ** 2) / (2 * sigma**2))

    # Continuum modulation along Y (slow flux variation + a couple of
    # narrow absorption features for visual interest)
    cont = 1.0 + 0.15 * np.sin(np.linspace(0, 4.0, ny))
    cont -= 0.6 * np.exp(-((np.arange(ny) - 250) ** 2) / (2 * 8.0**2))
    cont -= 0.45 * np.exp(-((np.arange(ny) - 720) ** 2) / (2 * 12.0**2))
    cont = np.clip(cont, 0.05, None)

    img = profile * cont[:, None] * 800.0
    img += 60.0  # sky
    img += rng.normal(0, 12.0, size=img.shape)  # readout / shot noise

    # A couple of "bad pixels" / cosmic-ish hits well outside the science box
    img[120, 30] = 4500
    img[800, 200] = 5200
    return img


def synth_timeseries(n: int = 240, rng: np.random.Generator | None = None):
    if rng is None:
        rng = np.random.default_rng(3)
    t = np.arange(n) * 1.3 / 60.0  # minutes (1.3 s SUTR cadence)
    dx = 0.05 * np.sin(t * 0.6) + rng.normal(0, 0.04, n)
    dy = 0.03 * np.cos(t * 0.4) + rng.normal(0, 0.05, n)
    fwhm = 3.4 + 0.15 * np.sin(t * 0.3) + rng.normal(0, 0.05, n)
    flux = 1.8e5 * (1 + 0.04 * np.sin(t * 0.2)) + rng.normal(0, 4e3, n)
    sky = 60 + 0.4 * t + rng.normal(0, 1.5, n)
    cmd_ra = -0.5 * dx                       # crude P controller for visual
    cmd_dec = -0.5 * dy

    # Insert one out-of-family stretch (clouds) where flux drops & we ALERT
    flux[150:175] *= 0.35
    cmd_ra[150:175] = np.nan
    cmd_dec[150:175] = np.nan
    return t, dx, dy, fwhm, flux, sky, cmd_ra, cmd_dec


# ---------------------------------------------------------------------------
# Figure layout
# ---------------------------------------------------------------------------

def render():
    img = synth_image()
    t, dx, dy, fwhm, flux, sky, cmd_ra, cmd_dec = synth_timeseries()

    fig = plt.figure(figsize=(15.5, 11.0), facecolor="#ECECEC")
    gs = GridSpec(
        nrows=8,
        ncols=2,
        height_ratios=[0.6, 4.5, 4.5, 0.5, 1.05, 1.05, 1.05, 1.05],
        width_ratios=[3.4, 2.2],
        hspace=0.45,
        wspace=0.10,
        left=0.045, right=0.985, top=0.965, bottom=0.05,
    )

    # ---- status bar (top) ---------------------------------------------------
    ax_status = fig.add_subplot(gs[0, :])
    ax_status.set_facecolor("#FAFAFA")
    ax_status.set_xticks([]); ax_status.set_yticks([])
    for s in ax_status.spines.values():
        s.set_edgecolor("#888"); s.set_linewidth(0.6)
    # Three "indicators"
    def dot(ax, x, color, label):
        ax.add_patch(mpatches.Circle(
            (x, 0.5), 0.07, transform=ax.transAxes, color=color, zorder=3,
            ec="#333", lw=0.4))
        ax.text(x + 0.018, 0.5, label, transform=ax.transAxes,
                va="center", ha="left", fontsize=9.5)
    dot(ax_status, 0.018, "#3aa55d", "TCS connected")
    dot(ax_status, 0.16,  "#3aa55d", "Watcher  4.2 s ago")
    dot(ax_status, 0.34,  "#3aa55d", "State: GUIDING")
    ax_status.text(0.55, 0.5, "Watch dir: /data/2026-04-30",
                   transform=ax_status.transAxes,
                   va="center", ha="left", fontsize=9.5)
    ax_status.add_patch(mpatches.FancyBboxPatch(
        (0.86, 0.18), 0.115, 0.62,
        boxstyle="round,pad=0.02,rounding_size=0.06",
        transform=ax_status.transAxes,
        facecolor="#FFFFFF", edgecolor="#666", lw=0.6, zorder=2))
    ax_status.text(0.917, 0.5, "Change…", transform=ax_status.transAxes,
                   va="center", ha="center", fontsize=9.5)

    # ---- live image (left, rows 1-2) ---------------------------------------
    ax_img = fig.add_subplot(gs[1:3, 0])
    ax_img.set_facecolor("#000")
    vlo, vhi = np.percentile(img, [2, 99.2])
    ax_img.imshow(img, origin="lower", cmap="viridis", vmin=vlo, vmax=vhi,
                  aspect="auto", interpolation="nearest")
    ax_img.set_title("Diff image  hen0042_017.fits  (K=1)",
                     fontsize=10, loc="left", color="#222")
    ax_img.set_xlabel("X (px)"); ax_img.set_ylabel("Y (px)")
    ax_img.tick_params(labelsize=8)

    # Boxes
    sci = mpatches.Rectangle((85, 100), 50, 824, ec="#E63946",
                             fc="none", lw=1.6, label="science box")
    bgL = mpatches.Rectangle((25, 100), 55, 824, ec="#F2A65A",
                             fc="none", lw=1.0, ls="--",
                             label="science bg L")
    bgR = mpatches.Rectangle((140, 100), 55, 824, ec="#F2A65A",
                             fc="none", lw=1.0, ls="--",
                             label="science bg R")
    ax_img.add_patch(sci); ax_img.add_patch(bgL); ax_img.add_patch(bgR)

    # Comparison box (smaller, lower SNR region)
    cmp_ = mpatches.Rectangle((95, 940), 30, 70, ec="#5BC0EB",
                              fc="none", lw=1.4, label="comparison box")
    ax_img.add_patch(cmp_)

    # Ridge line overlay (matches the synthetic ridge_x_center=110, angle=1.6°)
    yy = np.arange(100, 924, 2)
    xx = 110.0 + np.tan(np.deg2rad(1.6)) * (yy - img.shape[0] // 2)
    ax_img.plot(xx, yy, color="#E63946", lw=0.9, alpha=0.85, label="ridge")
    # Two ridge handles (visualization of edit mode)
    ax_img.scatter([xx[5], xx[-5]], [yy[5], yy[-5]],
                   s=42, c="#E63946", ec="white", lw=0.9, zorder=5)

    ax_img.legend(loc="upper right", fontsize=7.5, framealpha=0.85)

    # ---- control panel (right, rows 1-2) -----------------------------------
    ax_ctrl = fig.add_subplot(gs[1:3, 1])
    ax_ctrl.set_facecolor("#FAFAFA")
    ax_ctrl.set_xticks([]); ax_ctrl.set_yticks([])
    for s in ax_ctrl.spines.values():
        s.set_edgecolor("#888"); s.set_linewidth(0.6)
    ax_ctrl.set_xlim(0, 1); ax_ctrl.set_ylim(0, 1)

    def section(ax, y, title):
        ax.text(0.04, y, title, transform=ax.transAxes,
                fontsize=10.5, fontweight="bold", color="#222")

    def button(ax, x, y, w, h, label, primary=False):
        face = "#1f6feb" if primary else "#FFFFFF"
        edge = "#1f6feb" if primary else "#666"
        text_color = "white" if primary else "#222"
        ax.add_patch(mpatches.FancyBboxPatch(
            (x, y), w, h,
            boxstyle="round,pad=0.005,rounding_size=0.012",
            transform=ax.transAxes, facecolor=face, edgecolor=edge, lw=0.7))
        ax.text(x + w / 2, y + h / 2, label, transform=ax.transAxes,
                va="center", ha="center", fontsize=9, color=text_color)

    def field(ax, x, y, w, h, label, value):
        ax.text(x, y + h / 2, label, transform=ax.transAxes,
                va="center", ha="left", fontsize=9, color="#333")
        ax.add_patch(mpatches.Rectangle(
            (x + 0.42, y), w, h, transform=ax.transAxes,
            facecolor="white", edgecolor="#888", lw=0.6))
        ax.text(x + 0.42 + w - 0.012, y + h / 2, value, transform=ax.transAxes,
                va="center", ha="right", fontsize=9, color="#222",
                family="monospace")

    # Boxes section
    section(ax_ctrl, 0.965, "Boxes")
    button(ax_ctrl, 0.04, 0.910, 0.30, 0.040, "Draw science")
    button(ax_ctrl, 0.36, 0.910, 0.30, 0.040, "Add comparison")
    button(ax_ctrl, 0.04, 0.860, 0.30, 0.040, "Reset bg boxes")

    # Ridge section
    section(ax_ctrl, 0.815, "Ridge")
    field(ax_ctrl, 0.04, 0.770, 0.22, 0.038, "angle (deg):", "1.62")
    field(ax_ctrl, 0.04, 0.722, 0.22, 0.038, "x_center (px):", "110.42")
    button(ax_ctrl, 0.04, 0.668, 0.20, 0.040, "Auto-fit")
    button(ax_ctrl, 0.26, 0.668, 0.18, 0.040, "Edit")
    button(ax_ctrl, 0.46, 0.668, 0.30, 0.040, "Save reference", primary=True)

    # Targets section
    section(ax_ctrl, 0.615, "Targets (commandable)")
    field(ax_ctrl, 0.04, 0.570, 0.30, 0.038, "desired ridge_x:", "110.42")
    field(ax_ctrl, 0.04, 0.522, 0.30, 0.038, "desired ridge_y:", "512.00")

    # Loop section
    section(ax_ctrl, 0.460, "Loop")
    button(ax_ctrl, 0.04, 0.405, 0.40, 0.052, "START GUIDING", primary=True)
    button(ax_ctrl, 0.46, 0.405, 0.20, 0.052, "STOP")
    button(ax_ctrl, 0.68, 0.405, 0.26, 0.052, "PAUSE")

    # Live readouts
    section(ax_ctrl, 0.345, "Live readouts")
    field(ax_ctrl, 0.04, 0.300, 0.30, 0.038, "current dx:", "+0.04 px")
    field(ax_ctrl, 0.04, 0.255, 0.30, 0.038, "current dy:", "-0.02 px")
    field(ax_ctrl, 0.04, 0.210, 0.30, 0.038, "trace FWHM:", "3.42 px")
    field(ax_ctrl, 0.04, 0.165, 0.30, 0.038, "trace flux:", "1.84e5 ADU")
    field(ax_ctrl, 0.04, 0.120, 0.30, 0.038, "sky bg:", "62.1 ADU")

    # Tools
    section(ax_ctrl, 0.062, "Tools")
    button(ax_ctrl, 0.04, 0.012, 0.30, 0.040, "Estimate K…")
    button(ax_ctrl, 0.36, 0.012, 0.30, 0.040, "Settings…")

    # ---- alerts banner (row 3) ---------------------------------------------
    ax_alert = fig.add_subplot(gs[3, :])
    ax_alert.set_xticks([]); ax_alert.set_yticks([])
    ax_alert.set_facecolor("#F2A65A")
    for s in ax_alert.spines.values():
        s.set_edgecolor("#B5651D"); s.set_linewidth(0.7)
    ax_alert.text(
        0.012, 0.5,
        "ALERT  Out-of-family flux (5.2σ low) — commands suppressed; "
        "auto-resume after 3 in-family frames.",
        transform=ax_alert.transAxes, va="center", ha="left",
        fontsize=10, color="#3a2a10",
    )
    ax_alert.text(0.984, 0.5, "✕  dismiss", transform=ax_alert.transAxes,
                  va="center", ha="right", fontsize=9, color="#3a2a10")

    # ---- time series (rows 4-7) --------------------------------------------
    series = [
        ("dx, dy (px)",            [(t, dx, "dx", "#1f77b4"),
                                    (t, dy, "dy", "#d62728")]),
        ("FWHM (px)",              [(t, fwhm, None, "#2ca02c")]),
        ("trace flux (ADU)",       [(t, flux, "science", "#9467bd")]),
        ("commands (arcsec)",      [(t, cmd_ra, "RA",  "#1f77b4"),
                                    (t, cmd_dec, "Dec", "#d62728")]),
    ]

    for i, (ylabel, traces) in enumerate(series):
        ax = fig.add_subplot(gs[4 + i, :])
        ax.set_facecolor("#FFFFFF")
        for s in ax.spines.values():
            s.set_edgecolor("#999"); s.set_linewidth(0.5)
        # Mark the alerted region
        ax.axvspan(t[150], t[174], color="#F2A65A", alpha=0.18, lw=0)
        for tx, ty, lab, col in traces:
            ax.plot(tx, ty, color=col, lw=1.0, label=lab)
        if any(lab for _, _, lab, _ in traces):
            ax.legend(loc="upper right", fontsize=7.5, ncol=2, framealpha=0.85)
        if i == 0:
            ax.axhline(0, color="#888", lw=0.5, ls=":")
            ax.set_ylim(-0.25, 0.25)
        ax.set_ylabel(ylabel, fontsize=8.5)
        ax.tick_params(labelsize=7.5)
        if i == len(series) - 1:
            ax.set_xlabel("time (min)", fontsize=8.5)
        else:
            ax.set_xticklabels([])

    fig.suptitle(
        "Henrietta autoguider — operator GUI mockup (synthetic data)",
        fontsize=12, fontweight="bold", x=0.045, y=0.992, ha="left",
        color="#222",
    )

    fig.savefig(OUT_PATH, dpi=130)
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    render()
