"""Static mockup of the "Estimate K" modal dialog.

The Estimate K tool runs a small Monte Carlo on the current template
to recommend a value for `reduction.K` (the number of SUTR samples
averaged on each side of the K-window difference). For each candidate
K, it draws 50 noisy realisations of a guide image, runs each through
the full reduction pipeline (sky-subtract + 2-D xcor + parabolic peak),
and reports the RMS of the recovered (dx_px, dy_px).

This script renders a non-interactive PNG showing the layout the modal
should have. Run:

    python3 mockups/estimate_k_mockup.py
"""

from __future__ import annotations

import os

import matplotlib

matplotlib.use("Agg")

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.gridspec import GridSpec

OUT_PATH = os.path.join(os.path.dirname(__file__), "estimate_k_mockup.png")


def synth_estimate_k_results(rng: np.random.Generator | None = None):
    """Synthesise an Estimate K result set.

    Per-K shape: K=1 noisy, K=2..5 progressively tighter as 1/sqrt(K)
    falls off, but with a slight uptick at K=5 because the longer
    window starts to bridge real motion within the integration.
    """
    if rng is None:
        rng = np.random.default_rng(42)
    Ks = np.array([1, 2, 3, 4, 5])
    # Synthesised RMS per K (in pixels): falls as ~1/sqrt(K) with a
    # bottom around K=3 and a small bump at K=5.
    rms_x_truth = np.array([0.108, 0.078, 0.066, 0.069, 0.082])
    rms_y_truth = np.array([0.064, 0.045, 0.038, 0.041, 0.049])
    # Scatter from the 50 realisations -> Monte Carlo error bars
    rms_x_err = rms_x_truth / np.sqrt(2 * 50)
    rms_y_err = rms_y_truth / np.sqrt(2 * 50)
    return Ks, rms_x_truth, rms_x_err, rms_y_truth, rms_y_err


def render():
    Ks, rms_x, rms_x_err, rms_y, rms_y_err = synth_estimate_k_results()
    rms_total = np.sqrt(rms_x**2 + rms_y**2)
    recommended_K = int(Ks[np.argmin(rms_total)])

    fig = plt.figure(figsize=(11.5, 8.0), facecolor="#ECECEC")
    gs = GridSpec(
        nrows=4, ncols=2,
        height_ratios=[0.5, 4.0, 2.0, 0.7],
        width_ratios=[1.0, 1.0],
        hspace=0.45, wspace=0.18,
        left=0.07, right=0.97, top=0.93, bottom=0.05,
    )

    # ---- title bar (modal title) -----------------------------------------
    ax_title = fig.add_subplot(gs[0, :])
    ax_title.set_facecolor("#1f6feb")
    ax_title.set_xticks([]); ax_title.set_yticks([])
    for s in ax_title.spines.values():
        s.set_edgecolor("#1f6feb"); s.set_linewidth(0)
    ax_title.text(0.012, 0.5, "Estimate K — Monte Carlo on current template",
                  transform=ax_title.transAxes, va="center", ha="left",
                  fontsize=12, fontweight="bold", color="white")
    ax_title.text(0.985, 0.5, "✕", transform=ax_title.transAxes,
                  va="center", ha="right", fontsize=14, color="white")

    # ---- RMS vs K plot (left, top) ---------------------------------------
    ax_plot = fig.add_subplot(gs[1, 0])
    ax_plot.errorbar(Ks, rms_x, yerr=rms_x_err, fmt="o-", color="#1f77b4",
                     lw=1.4, ms=6, capsize=3, label="RMS(dx)")
    ax_plot.errorbar(Ks, rms_y, yerr=rms_y_err, fmt="s-", color="#d62728",
                     lw=1.4, ms=6, capsize=3, label="RMS(dy)")
    ax_plot.errorbar(Ks, rms_total, yerr=np.sqrt(rms_x_err**2 + rms_y_err**2),
                     fmt="^--", color="#444", lw=1.0, ms=6, capsize=3,
                     label="RMS(total)")
    ax_plot.axvline(recommended_K, color="#3aa55d", ls=":", lw=1.5,
                    alpha=0.7)
    ax_plot.text(recommended_K + 0.1, ax_plot.get_ylim()[1] * 0.95,
                 f" recommended K = {recommended_K}",
                 color="#3aa55d", fontsize=10, va="top")
    ax_plot.set_xlabel("K  (SUTR samples averaged per side)", fontsize=10)
    ax_plot.set_ylabel("centroid RMS (px)", fontsize=10)
    ax_plot.set_xticks(Ks)
    ax_plot.set_title("Centroid RMS from 50 Monte Carlo realisations per K",
                      fontsize=10, loc="left", pad=6)
    ax_plot.legend(loc="upper right", fontsize=9, framealpha=0.9)
    ax_plot.grid(True, alpha=0.25, lw=0.4)
    ax_plot.tick_params(labelsize=9)

    # ---- result table (right, top) ---------------------------------------
    ax_table = fig.add_subplot(gs[1, 1])
    ax_table.set_xticks([]); ax_table.set_yticks([])
    for s in ax_table.spines.values():
        s.set_edgecolor("#888"); s.set_linewidth(0.5)
    ax_table.set_xlim(0, 1); ax_table.set_ylim(0, 1)
    ax_table.set_facecolor("#FFFFFF")
    # Header
    ax_table.text(0.5, 0.95, "Result table",
                  transform=ax_table.transAxes, va="top", ha="center",
                  fontsize=11, fontweight="bold", color="#222")
    # Column headers
    headers = ["K", "RMS(dx) [px]", "RMS(dy) [px]", "RMS(total) [px]"]
    col_x   = [0.10, 0.32, 0.55, 0.78]
    y_top   = 0.84
    for x, h in zip(col_x, headers):
        ax_table.text(x, y_top, h, transform=ax_table.transAxes,
                      va="top", ha="left", fontsize=9.5,
                      fontweight="bold", color="#222")
    ax_table.plot([0.05, 0.95], [y_top - 0.04, y_top - 0.04],
                  transform=ax_table.transAxes, color="#888", lw=0.6,
                  clip_on=False)
    # Rows
    for i, (K, x_, x_err, y_, y_err, tot) in enumerate(
        zip(Ks, rms_x, rms_x_err, rms_y, rms_y_err, rms_total)
    ):
        y_row = y_top - 0.10 - i * 0.10
        is_rec = (K == recommended_K)
        if is_rec:
            ax_table.add_patch(mpatches.Rectangle(
                (0.04, y_row - 0.04), 0.92, 0.08,
                transform=ax_table.transAxes,
                facecolor="#E8F5E9", edgecolor="none", zorder=0))
        weight = "bold" if is_rec else "normal"
        color  = "#1b5e20" if is_rec else "#222"
        ax_table.text(col_x[0], y_row, str(K),
                      transform=ax_table.transAxes, fontsize=10,
                      fontweight=weight, color=color, family="monospace")
        ax_table.text(col_x[1], y_row, f"{x_:.3f} ± {x_err:.3f}",
                      transform=ax_table.transAxes, fontsize=10,
                      fontweight=weight, color=color, family="monospace")
        ax_table.text(col_x[2], y_row, f"{y_:.3f} ± {y_err:.3f}",
                      transform=ax_table.transAxes, fontsize=10,
                      fontweight=weight, color=color, family="monospace")
        ax_table.text(col_x[3], y_row, f"{tot:.3f}",
                      transform=ax_table.transAxes, fontsize=10,
                      fontweight=weight, color=color, family="monospace")
    # Footer note
    ax_table.text(
        0.5, 0.05,
        f"Recommended: K = {recommended_K}  (smallest total RMS)",
        transform=ax_table.transAxes, va="bottom", ha="center",
        fontsize=10, color="#1b5e20", fontweight="bold",
    )

    # ---- inputs / parameters (bottom-left) -------------------------------
    ax_inputs = fig.add_subplot(gs[2, 0])
    ax_inputs.set_facecolor("#FAFAFA")
    ax_inputs.set_xticks([]); ax_inputs.set_yticks([])
    for s in ax_inputs.spines.values():
        s.set_edgecolor("#888"); s.set_linewidth(0.5)
    ax_inputs.set_xlim(0, 1); ax_inputs.set_ylim(0, 1)
    ax_inputs.text(0.04, 0.92, "Monte Carlo inputs",
                   transform=ax_inputs.transAxes, va="top", ha="left",
                   fontsize=10.5, fontweight="bold", color="#222")

    def kv(ax, x, y, k, v):
        ax.text(x, y, k, transform=ax.transAxes,
                va="center", ha="left", fontsize=9.5, color="#444")
        ax.text(x + 0.55, y, v, transform=ax.transAxes,
                va="center", ha="left", fontsize=9.5, color="#222",
                family="monospace")

    kv(ax_inputs, 0.04, 0.78, "Template source:",       "hen0042.fits")
    kv(ax_inputs, 0.04, 0.66, "Stamp size:",            "50 × 1380 px")
    kv(ax_inputs, 0.04, 0.54, "Unmasked pixels:",       "67,824")
    kv(ax_inputs, 0.04, 0.42, "Detector gain:",         "4.0  e⁻/DN")
    kv(ax_inputs, 0.04, 0.30, "Detector RN:",           "12.0  e⁻ / read")
    kv(ax_inputs, 0.04, 0.18, "Realisations per K:",    "50")
    kv(ax_inputs, 0.04, 0.06, "K range tested:",        "1, 2, 3, 4, 5")

    # ---- progress / status (bottom-right) --------------------------------
    ax_status = fig.add_subplot(gs[2, 1])
    ax_status.set_facecolor("#FAFAFA")
    ax_status.set_xticks([]); ax_status.set_yticks([])
    for s in ax_status.spines.values():
        s.set_edgecolor("#888"); s.set_linewidth(0.5)
    ax_status.set_xlim(0, 1); ax_status.set_ylim(0, 1)
    ax_status.text(0.04, 0.92, "Run status",
                   transform=ax_status.transAxes, va="top", ha="left",
                   fontsize=10.5, fontweight="bold", color="#222")
    ax_status.text(0.04, 0.78, "Elapsed:    1.4 s",
                   transform=ax_status.transAxes, va="center",
                   fontsize=9.5, color="#444", family="monospace")
    ax_status.text(0.04, 0.66, "Remaining:  0.0 s",
                   transform=ax_status.transAxes, va="center",
                   fontsize=9.5, color="#444", family="monospace")
    ax_status.text(0.04, 0.54, "Status:     COMPLETE",
                   transform=ax_status.transAxes, va="center",
                   fontsize=9.5, color="#1b5e20", family="monospace",
                   fontweight="bold")
    # Progress bar
    ax_status.add_patch(mpatches.Rectangle(
        (0.04, 0.32), 0.92, 0.06, transform=ax_status.transAxes,
        facecolor="white", edgecolor="#888", lw=0.6))
    ax_status.add_patch(mpatches.Rectangle(
        (0.04, 0.32), 0.92, 0.06, transform=ax_status.transAxes,
        facecolor="#3aa55d", edgecolor="none"))
    ax_status.text(0.5, 0.35, "250 / 250 realisations",
                   transform=ax_status.transAxes, va="center", ha="center",
                   fontsize=9, color="white", fontweight="bold")
    ax_status.text(0.04, 0.20,
                   "Runs on a dedicated short-lived worker thread.",
                   transform=ax_status.transAxes, va="center", ha="left",
                   fontsize=8.5, color="#666", fontstyle="italic")
    ax_status.text(0.04, 0.10,
                   "Live guiding is unaffected during the run.",
                   transform=ax_status.transAxes, va="center", ha="left",
                   fontsize=8.5, color="#666", fontstyle="italic")

    # ---- footer buttons ---------------------------------------------------
    ax_btn = fig.add_subplot(gs[3, :])
    ax_btn.set_facecolor("#ECECEC")
    ax_btn.set_xticks([]); ax_btn.set_yticks([])
    for s in ax_btn.spines.values():
        s.set_edgecolor("none"); s.set_linewidth(0)
    ax_btn.set_xlim(0, 1); ax_btn.set_ylim(0, 1)

    def button(ax, x, y, w, h, label, primary=False):
        face = "#1f6feb" if primary else "#FFFFFF"
        edge = "#1f6feb" if primary else "#666"
        text_color = "white" if primary else "#222"
        ax.add_patch(mpatches.FancyBboxPatch(
            (x, y), w, h,
            boxstyle="round,pad=0.005,rounding_size=0.012",
            transform=ax.transAxes, facecolor=face, edgecolor=edge, lw=0.7))
        ax.text(x + w / 2, y + h / 2, label, transform=ax.transAxes,
                va="center", ha="center", fontsize=10, color=text_color)

    button(ax_btn, 0.40, 0.20, 0.16, 0.55, "Re-run")
    button(ax_btn, 0.58, 0.20, 0.16, 0.55, "Cancel")
    button(ax_btn, 0.76, 0.20, 0.20, 0.55,
           f"Apply K = {recommended_K}", primary=True)

    fig.suptitle("Estimate K — modal dialog mockup",
                 fontsize=12, fontweight="bold",
                 x=0.07, y=0.985, ha="left", color="#222")

    fig.savefig(OUT_PATH, dpi=130)
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    render()
