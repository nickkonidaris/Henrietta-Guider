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

import matplotlib

matplotlib.use("Agg")  # headless render — no GUI backend needed

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
    trace_x_center: float = 110.0,
    trace_tilt_deg: float = 1.6,
    fwhm_px: float = 3.5,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """Build a fake guide-image: a tilted near-vertical trace with a couple
    of absorption features, plus sky and noise.

    The trace tilt is just for visual realism — the autoguider treats the
    stamp as an opaque template and never fits a ridge.
    """
    if rng is None:
        rng = np.random.default_rng(7)
    y_full = np.arange(ny) - ny // 2  # zero-centred at detector middle row
    x_grid = np.arange(nx)[None, :]

    angle_rad = np.deg2rad(trace_tilt_deg)
    x_trace = trace_x_center + np.tan(angle_rad) * y_full

    sigma = fwhm_px / 2.355
    profile = np.exp(-((x_grid - x_trace[:, None]) ** 2) / (2 * sigma**2))

    # Continuum modulation along Y (slow flux variation + a couple of
    # narrow absorption features for visual interest).
    cont = 1.0 + 0.15 * np.sin(np.linspace(0, 4.0, ny))
    cont -= 0.6 * np.exp(-((np.arange(ny) - 250) ** 2) / (2 * 8.0**2))
    cont -= 0.45 * np.exp(-((np.arange(ny) - 720) ** 2) / (2 * 12.0**2))
    cont = np.clip(cont, 0.05, None)

    img = profile * cont[:, None] * 800.0
    img += 60.0  # sky
    img += rng.normal(0, 12.0, size=img.shape)  # readout / shot noise

    # A couple of "bad pixels" / cosmic-ish hits well outside the science stamp
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
    flux_cmp = 0.40 * flux + rng.normal(0, 1.5e3, n)
    sky = 60 + 0.4 * t + rng.normal(0, 1.5, n)
    # xcor peak value: dimensionless correlation peak — drops on cloud
    xcor = 0.92 + rng.normal(0, 0.01, n)

    # Insert one out-of-family stretch (clouds) where flux drops & we ALERT
    flux[150:175] *= 0.35
    flux_cmp[150:175] *= 0.35
    xcor[150:175] *= 0.45  # xcor peak also drops sharply when clouded
    return t, dx, dy, fwhm, flux, flux_cmp, sky, xcor


def synth_signal_snr(t: np.ndarray,
                     rng: np.random.Generator | None = None) -> np.ndarray:
    """Synthetic per-stamp signal_snr time series.

    Rises through each integration as more reads accumulate, then resets
    sharply at each frame-number boundary. Roughly 23 SUTRs per
    integration at the default 1.3 s cadence.
    """
    if rng is None:
        rng = np.random.default_rng(11)
    # frame boundary every ~30s (23 SUTRs × 1.3 s). t is in minutes.
    integration_s = 30.0 / 60.0  # 0.5 min per integration
    phase = (t % integration_s) / integration_s   # 0 -> 1 within an integration
    # SNR growth ~ sqrt(t) within an integration; saturates at end.
    snr = 220.0 * np.sqrt(phase + 0.05) + rng.normal(0, 4, t.size)
    # Clouds: knock down SNR during the alert region.
    cloud_mask = (t > t[150]) & (t < t[174])
    snr[cloud_mask] *= 0.45
    return snr


# ---------------------------------------------------------------------------
# Figure layout
# ---------------------------------------------------------------------------

def render():
    img = synth_image()
    t, dx, dy, fwhm, flux, flux_cmp, sky, xcor = synth_timeseries()
    signal_snr = synth_signal_snr(t)

    fig = plt.figure(figsize=(15.5, 12.5), facecolor="#ECECEC")
    gs = GridSpec(
        nrows=10,
        ncols=2,
        height_ratios=[0.6, 4.5, 4.5, 0.5,
                       0.85, 0.85, 0.85, 0.85, 0.85, 0.85],  # 6 time-series rows
        width_ratios=[3.4, 2.2],
        hspace=0.50,
        wspace=0.10,
        left=0.045, right=0.985, top=0.965, bottom=0.045,
    )

    # ---- status bar (top) ---------------------------------------------------
    ax_status = fig.add_subplot(gs[0, :])
    ax_status.set_facecolor("#FAFAFA")
    ax_status.set_xticks([]); ax_status.set_yticks([])
    for s in ax_status.spines.values():
        s.set_edgecolor("#888"); s.set_linewidth(0.6)

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

    # Stamps. Science stamp covers the whole illuminated stripe (Y wide,
    # X snug ±25 around the trace). No flanking bg boxes — the autoguider
    # subtracts row-by-row local sky from the outer 1/6 of the stamp itself.
    sci_stamp = mpatches.Rectangle(
        (85, 50), 50, 924,
        ec="#E63946", fc="none", lw=1.6, label="science stamp",
    )
    ax_img.add_patch(sci_stamp)
    # Annotate the outer 1/6 sky bands inside the stamp
    edge_w = 50 // 6
    sky_left = mpatches.Rectangle(
        (85, 50), edge_w, 924,
        ec="none", fc="#E63946", alpha=0.10,
    )
    sky_right = mpatches.Rectangle(
        (85 + 50 - edge_w, 50), edge_w, 924,
        ec="none", fc="#E63946", alpha=0.10,
    )
    ax_img.add_patch(sky_left); ax_img.add_patch(sky_right)
    ax_img.text(
        85 + edge_w / 2, 990, "sky", color="#E63946",
        fontsize=7, ha="center", va="bottom", alpha=0.85,
    )
    ax_img.text(
        85 + 50 - edge_w / 2, 990, "sky", color="#E63946",
        fontsize=7, ha="center", va="bottom", alpha=0.85,
    )

    # Comparison stamp (smaller, lower SNR region)
    cmp_stamp = mpatches.Rectangle(
        (95, 770), 30, 200,
        ec="#5BC0EB", fc="none", lw=1.4, label="comparison stamp",
    )
    ax_img.add_patch(cmp_stamp)

    # Template thumbnail inset (small) — show what the template looks like
    inset = ax_img.inset_axes([0.79, 0.04, 0.18, 0.30])
    inset.imshow(
        img[50:974, 85:135],
        origin="lower", cmap="viridis", vmin=vlo, vmax=vhi,
        aspect="auto", interpolation="nearest",
    )
    inset.set_xticks([]); inset.set_yticks([])
    for s in inset.spines.values():
        s.set_edgecolor("#E63946"); s.set_linewidth(1.2)
    inset.set_title("template (hen0042)", fontsize=7, color="#E63946", pad=2)

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

    def button(ax, x, y, w, h, label, primary=False, disabled=False):
        if disabled:
            face, edge, text_color = "#E5E5E5", "#BBB", "#999"
        elif primary:
            face, edge, text_color = "#1f6feb", "#1f6feb", "white"
        else:
            face, edge, text_color = "#FFFFFF", "#666", "#222"
        ax.add_patch(mpatches.FancyBboxPatch(
            (x, y), w, h,
            boxstyle="round,pad=0.005,rounding_size=0.012",
            transform=ax.transAxes, facecolor=face, edgecolor=edge, lw=0.7))
        ax.text(x + w / 2, y + h / 2, label, transform=ax.transAxes,
                va="center", ha="center", fontsize=9, color=text_color)

    def field(ax, x, y, w, h, label, value, label_w=0.38):
        ax.text(x, y + h / 2, label, transform=ax.transAxes,
                va="center", ha="left", fontsize=9, color="#333")
        ax.add_patch(mpatches.Rectangle(
            (x + label_w, y), w, h, transform=ax.transAxes,
            facecolor="white", edgecolor="#888", lw=0.6))
        ax.text(x + label_w + w - 0.012, y + h / 2, value, transform=ax.transAxes,
                va="center", ha="right", fontsize=9, color="#222",
                family="monospace")

    # Stamps section
    section(ax_ctrl, 0.965, "Stamps")
    button(ax_ctrl, 0.04, 0.910, 0.30, 0.040, "Draw science")
    button(ax_ctrl, 0.36, 0.910, 0.30, 0.040, "Add comparison")
    button(ax_ctrl, 0.04, 0.860, 0.30, 0.040, "Reset to defaults")

    # Stamp geometry section
    section(ax_ctrl, 0.815, "Stamp geometry (science)")
    field(ax_ctrl, 0.04, 0.770, 0.22, 0.038, "x_center:",     "110 px")
    field(ax_ctrl, 0.04, 0.722, 0.22, 0.038, "x_halfwidth:",  " 25 px")
    field(ax_ctrl, 0.04, 0.674, 0.22, 0.038, "y_lo:",         "600 px")
    field(ax_ctrl, 0.04, 0.626, 0.22, 0.038, "y_hi:",         "1980 px")

    # Template section
    section(ax_ctrl, 0.575, "Template")
    ax_ctrl.text(0.04, 0.530, "current: hen0042.fits  (fixed)",
                 transform=ax_ctrl.transAxes,
                 va="center", ha="left", fontsize=9, color="#333")
    # Unchecked checkbox + label
    ax_ctrl.add_patch(mpatches.Rectangle(
        (0.04, 0.482), 0.022, 0.030, transform=ax_ctrl.transAxes,
        facecolor="white", edgecolor="#666", lw=0.7))
    ax_ctrl.text(0.075, 0.497, "Auto-refresh on new henNNNN.fits",
                 transform=ax_ctrl.transAxes,
                 va="center", ha="left", fontsize=8.8, color="#444")
    button(ax_ctrl, 0.04, 0.430, 0.92, 0.040, "Build Template", primary=True)

    # Loop section
    section(ax_ctrl, 0.395, "Loop")
    button(ax_ctrl, 0.04, 0.336, 0.40, 0.052, "START GUIDING", primary=True)
    button(ax_ctrl, 0.46, 0.336, 0.20, 0.052, "STOP")
    button(ax_ctrl, 0.68, 0.336, 0.26, 0.052, "PAUSE")

    # Live readouts
    section(ax_ctrl, 0.282, "Live readouts")
    field(ax_ctrl, 0.04, 0.230, 0.30, 0.036, "current dx:",    "+0.04 px")
    field(ax_ctrl, 0.04, 0.185, 0.30, 0.036, "current dy:",    "-0.02 px")
    field(ax_ctrl, 0.04, 0.140, 0.30, 0.036, "trace FWHM:",    "3.42 px")
    field(ax_ctrl, 0.04, 0.095, 0.30, 0.036, "xcor peak:",     "0.927")

    # Tools
    section(ax_ctrl, 0.048, "Tools")
    button(ax_ctrl, 0.04, 0.005, 0.30, 0.034, "Estimate K…")
    button(ax_ctrl, 0.36, 0.005, 0.30, 0.034, "Settings…")

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

    # ---- time series (rows 4-8) --------------------------------------------
    series = [
        ("dx, dy (px)",              [(t, dx, "dx", "#1f77b4"),
                                      (t, dy, "dy", "#d62728")]),
        ("FWHM (px)",                [(t, fwhm, None, "#2ca02c")]),
        ("trace flux (ADU)",         [(t, flux,     "science",    "#9467bd"),
                                      (t, flux_cmp, "comparison", "#9467bd")]),
        ("sky bg (ADU)",             [(t, sky, None, "#7f7f7f")]),
        ("xcor peak",                [(t, xcor, None, "#e377c2")]),
        (r"signal SNR  $\sqrt{e^-}$", [(t, signal_snr, None, "#ff7f0e")]),
    ]

    for i, (ylabel, traces) in enumerate(series):
        ax = fig.add_subplot(gs[4 + i, :])
        ax.set_facecolor("#FFFFFF")
        for s in ax.spines.values():
            s.set_edgecolor("#999"); s.set_linewidth(0.5)
        ax.axvspan(t[150], t[174], color="#F2A65A", alpha=0.18, lw=0)
        for tx, ty, lab, col in traces:
            if lab == "comparison":
                ax.plot(tx, ty, color=col, lw=0.8, ls="--", label=lab)
            else:
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
