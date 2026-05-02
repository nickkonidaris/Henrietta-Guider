"""GUI mockup populated with real simulator output.

Runs sim_replay's inline pipeline against test/ frames, then renders the
operator GUI layout (per mockups/gui_mockup.png) with:

  - the LAST guide image as the live image (sky-subtracted hen1765 diff
    near end of run);
  - the science / comparison stamp overlays;
  - the template thumbnail in the top-right of the image axes;
  - all six time-series panels filled with the simulator's history;
  - the alert banner left empty (no alert in this synthetic run);
  - the SNR histogram populated from the actual stamp's pixel SNRs.

Output: experiments/sim_gui_snapshot.png

Run: .venv/bin/python experiments/sim_gui_snapshot.py
"""

from __future__ import annotations

import re
import socket
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
from astropy.io import fits
from matplotlib.gridspec import GridSpec

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
OUT_PATH = REPO / "experiments" / "sim_gui_snapshot.png"

SUTR_RE = re.compile(r"^hen(\d{4})_(\d{3})r\.fits$")

STAMP = Stamp(x_center=1024, x_halfwidth=25, y_lo=360, y_hi=1990)
PLATE_SCALE = 0.435
GAIN_E_PER_DN = 4.0


@dataclass
class Sample:
    frame: int
    sutr: int
    dx_px: float | None
    dy_px: float | None
    cmd_ra: float | None
    cmd_dec: float | None
    flux: float | None
    sky: float | None
    fwhm: float | None
    xcor_peak: float | None
    signal_snr: float | None


def find_sutrs(d: Path) -> list[tuple[int, int, Path]]:
    out = []
    for p in sorted(d.iterdir()):
        m = SUTR_RE.match(p.name)
        if m:
            out.append((int(m.group(1)), int(m.group(2)), p))
    return sorted(out)


def simulate() -> tuple[list[Sample], np.ndarray, np.ndarray, np.ndarray]:
    """Returns (history, last_sub, template_image, last_signal_snr_per_pixel)."""
    good_full = load_bpm(BPM_PATH)
    template = build_template(TEST_DIR / "hen1764.fits", STAMP, good_full)
    fb = FrameBuffer(K=1, stride=1)
    cfg = ControllerConfig(
        Kp=0.5, Ki=0.0, Kd=0.0,
        deadband_arcsec=0.025, max_command_arcsec=2.45,
    )
    ctrl_ra = Controller(cfg)
    ctrl_dec = Controller(cfg)
    a, b = socket.socketpair()
    server = AutoGuiderServer.from_connected_socket(a, pacing_interval_s=0.0)

    good_stamp = good_full[STAMP.y_lo:STAMP.y_hi, STAMP.x_min:STAMP.x_max]

    history: list[Sample] = []
    last_sub: np.ndarray | None = None
    last_snr: np.ndarray | None = None
    reset_read: np.ndarray | None = None
    reset_frame: int | None = None

    for frame, sutr, path in find_sutrs(TEST_DIR):
        with fits.open(path) as hdul:
            raw = hdul[0].data.astype(np.float32)

        if reset_frame != frame:
            reset_read = raw.copy()
            reset_frame = frame

        # signal SNR (cumulative since reset, in stamp).
        if reset_read is not None:
            sig_dn = (raw[STAMP.y_lo:STAMP.y_hi, STAMP.x_min:STAMP.x_max]
                      - reset_read[STAMP.y_lo:STAMP.y_hi, STAMP.x_min:STAMP.x_max])
            sig_e_total = float(np.sum(np.where(good_stamp, sig_dn, 0.0))) * GAIN_E_PER_DN
            ssnr = float(np.sqrt(max(sig_e_total, 0.0)))
            sig_e_per_pix = np.clip(sig_dn * GAIN_E_PER_DN, 0, None)
            snr_per_pix = np.sqrt(sig_e_per_pix)
        else:
            ssnr = None
            snr_per_pix = None

        guide_full = fb.add(frame, sutr, raw)
        if guide_full is None:
            history.append(Sample(frame, sutr, None, None, None, None,
                                   None, None, None, None, ssnr))
            continue

        stamp_img = guide_full[STAMP.y_lo:STAMP.y_hi, STAMP.x_min:STAMP.x_max]
        sub, per_row_sky = subtract_local_sky(stamp_img, good_stamp)
        sub = np.where(good_stamp, sub, 0.0)

        xc = xcor_2d(sub, template.image, search=4)
        dra, ddec = detector_to_sky(xc.dx_px, xc.dy_px, PLATE_SCALE, 0.0, +1, +1)
        cmd_ra = ctrl_ra.step(dra)
        cmd_dec = ctrl_dec.step(ddec)
        sent = server.send_guide(cmd_ra, cmd_dec)
        if sent:
            b.recv(6)

        flux = float(np.sum(sub))
        sky = float(np.median(per_row_sky))
        # Crude FWHM from second moment along X.
        prof = sub.sum(axis=0)
        x_arr = np.arange(prof.size)
        if prof.sum() > 0:
            x_mean = float(np.sum(x_arr * prof) / np.sum(prof))
            x_var = float(np.sum((x_arr - x_mean) ** 2 * prof) / np.sum(prof))
            fwhm = float(2.355 * np.sqrt(max(x_var, 0)))
        else:
            fwhm = float("nan")

        history.append(Sample(
            frame=frame, sutr=sutr,
            dx_px=xc.dx_px, dy_px=xc.dy_px,
            cmd_ra=cmd_ra, cmd_dec=cmd_dec,
            flux=flux, sky=sky, fwhm=fwhm,
            xcor_peak=xc.peak_value, signal_snr=ssnr,
        ))
        last_sub = sub
        last_snr = snr_per_pix

    a.close()
    b.close()
    assert last_sub is not None
    assert last_snr is not None
    return history, last_sub, template.image, last_snr


# --- GUI render ----------------------------------------------------------

def section(ax, y, title):
    ax.text(0.04, y, title, transform=ax.transAxes,
            fontsize=10.5, fontweight="bold", color="#222")


def field(ax, x, y, w, h, label, value, label_w=0.38):
    ax.text(x, y + h / 2, label, transform=ax.transAxes,
            va="center", ha="left", fontsize=9, color="#333")
    ax.add_patch(mpatches.Rectangle(
        (x + label_w, y), w, h, transform=ax.transAxes,
        facecolor="white", edgecolor="#888", lw=0.6,
    ))
    ax.text(x + label_w + w - 0.012, y + h / 2, value,
            transform=ax.transAxes, va="center", ha="right",
            fontsize=9, color="#222", family="monospace")


def button(ax, x, y, w, h, label, primary=False):
    face = "#1f6feb" if primary else "#FFFFFF"
    edge = "#1f6feb" if primary else "#666"
    text_color = "white" if primary else "#222"
    ax.add_patch(mpatches.FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0.005,rounding_size=0.012",
        transform=ax.transAxes, facecolor=face, edgecolor=edge, lw=0.7,
    ))
    ax.text(x + w / 2, y + h / 2, label, transform=ax.transAxes,
            va="center", ha="center", fontsize=9, color=text_color)


def render(history: list[Sample], last_sub: np.ndarray,
           template_image: np.ndarray, last_snr: np.ndarray) -> None:
    last = next(h for h in reversed(history) if h.dx_px is not None)
    fig = plt.figure(figsize=(15.5, 11.5), facecolor="#ECECEC")
    gs = GridSpec(
        nrows=9, ncols=2,
        height_ratios=[0.6, 4.5, 4.5, 0.5, 0.85, 0.85, 0.85, 0.85, 1.6],
        width_ratios=[3.4, 2.2],
        hspace=0.55, wspace=0.10,
        left=0.045, right=0.985, top=0.965, bottom=0.05,
    )

    # Status bar.
    ax_st = fig.add_subplot(gs[0, :])
    ax_st.set_facecolor("#FAFAFA")
    ax_st.set_xticks([]); ax_st.set_yticks([])
    for s in ax_st.spines.values():
        s.set_edgecolor("#888"); s.set_linewidth(0.6)
    ax_st.add_patch(mpatches.Circle((0.018, 0.5), 0.07,
                                     transform=ax_st.transAxes,
                                     color="#3aa55d", ec="#333", lw=0.4))
    ax_st.text(0.034, 0.5, "TCS connected",
               transform=ax_st.transAxes, va="center", fontsize=9.5)
    ax_st.add_patch(mpatches.Circle((0.16, 0.5), 0.07,
                                     transform=ax_st.transAxes,
                                     color="#3aa55d", ec="#333", lw=0.4))
    ax_st.text(0.176, 0.5, f"Watcher  hen{last.frame:04d}_{last.sutr:03d}r.fits",
               transform=ax_st.transAxes, va="center", fontsize=9.5)
    ax_st.add_patch(mpatches.Circle((0.40, 0.5), 0.07,
                                     transform=ax_st.transAxes,
                                     color="#3aa55d", ec="#333", lw=0.4))
    ax_st.text(0.416, 0.5, "State: GUIDING",
               transform=ax_st.transAxes, va="center", fontsize=9.5)
    ax_st.text(0.60, 0.5, "Watch dir: /Users/npk/.../test",
               transform=ax_st.transAxes, va="center", fontsize=9.5)
    ax_st.add_patch(mpatches.FancyBboxPatch(
        (0.86, 0.18), 0.115, 0.62,
        boxstyle="round,pad=0.02,rounding_size=0.06",
        transform=ax_st.transAxes,
        facecolor="white", edgecolor="#666", lw=0.6, zorder=2,
    ))
    ax_st.text(0.917, 0.5, "Change…",
               transform=ax_st.transAxes, va="center", ha="center",
               fontsize=9.5)

    # Live image.
    ax_img = fig.add_subplot(gs[1:3, 0])
    ax_img.set_facecolor("#000")
    vlo, vhi = np.percentile(last_sub, [2, 99])
    ax_img.imshow(last_sub, origin="lower", cmap="viridis", aspect="auto",
                  vmin=vlo, vmax=vhi, interpolation="nearest",
                  extent=[STAMP.x_min, STAMP.x_max, STAMP.y_lo, STAMP.y_hi])
    ax_img.set_title(
        f"Diff image  hen{last.frame:04d}_{last.sutr:03d}r.fits  "
        f"(sky-subtracted, mask-applied)",
        fontsize=10, loc="left",
    )
    ax_img.set_xlabel("X (px)"); ax_img.set_ylabel("Y (px)")
    sci = mpatches.Rectangle(
        (STAMP.x_min, STAMP.y_lo),
        STAMP.x_max - STAMP.x_min, STAMP.y_hi - STAMP.y_lo,
        ec="#E63946", fc="none", lw=1.6, label="science stamp",
    )
    ax_img.add_patch(sci)
    # Template thumbnail inset.
    inset = ax_img.inset_axes([0.79, 0.04, 0.18, 0.30])
    inset.imshow(template_image, origin="lower", cmap="viridis", aspect="auto",
                 vmin=vlo, vmax=vhi, interpolation="nearest")
    inset.set_xticks([]); inset.set_yticks([])
    for s in inset.spines.values():
        s.set_edgecolor("#E63946"); s.set_linewidth(1.2)
    inset.set_title("template (hen1764)", fontsize=7, color="#E63946", pad=2)
    ax_img.legend(loc="upper right", fontsize=7.5, framealpha=0.85)

    # Control panel.
    ax_c = fig.add_subplot(gs[1:3, 1])
    ax_c.set_facecolor("#FAFAFA")
    ax_c.set_xticks([]); ax_c.set_yticks([])
    for s in ax_c.spines.values():
        s.set_edgecolor("#888"); s.set_linewidth(0.6)
    ax_c.set_xlim(0, 1); ax_c.set_ylim(0, 1)

    section(ax_c, 0.965, "Stamps")
    button(ax_c, 0.04, 0.910, 0.30, 0.040, "Draw science")
    button(ax_c, 0.36, 0.910, 0.30, 0.040, "Add comparison")
    button(ax_c, 0.04, 0.860, 0.30, 0.040, "Reset to defaults")

    section(ax_c, 0.815, "Stamp geometry (science)")
    field(ax_c, 0.04, 0.770, 0.22, 0.038, "x_center:",    f"{STAMP.x_center} px")
    field(ax_c, 0.04, 0.722, 0.22, 0.038, "x_halfwidth:", f" {STAMP.x_halfwidth} px")
    field(ax_c, 0.04, 0.674, 0.22, 0.038, "y_lo:",        f"{STAMP.y_lo} px")
    field(ax_c, 0.04, 0.626, 0.22, 0.038, "y_hi:",        f"{STAMP.y_hi} px")

    section(ax_c, 0.575, "Template")
    ax_c.text(0.04, 0.530, "current: hen1764.fits  (fixed)",
              transform=ax_c.transAxes, va="center", fontsize=9, color="#333")
    ax_c.add_patch(mpatches.Rectangle(
        (0.04, 0.482), 0.022, 0.030,
        transform=ax_c.transAxes, facecolor="white",
        edgecolor="#666", lw=0.7,
    ))
    ax_c.text(0.075, 0.497, "Auto-refresh on new henNNNN.fits",
              transform=ax_c.transAxes, va="center", fontsize=8.8, color="#444")
    button(ax_c, 0.04, 0.430, 0.92, 0.040, "Build Template", primary=True)

    section(ax_c, 0.395, "Loop")
    button(ax_c, 0.04, 0.336, 0.40, 0.052, "START GUIDING", primary=True)
    button(ax_c, 0.46, 0.336, 0.20, 0.052, "STOP")
    button(ax_c, 0.68, 0.336, 0.26, 0.052, "PAUSE")

    section(ax_c, 0.282, "Live readouts")
    field(ax_c, 0.04, 0.230, 0.30, 0.036, "current dx:", f"{last.dx_px:+.3f} px")
    field(ax_c, 0.04, 0.185, 0.30, 0.036, "current dy:", f"{last.dy_px:+.3f} px")
    field(ax_c, 0.04, 0.140, 0.30, 0.036, "trace FWHM:", f"{last.fwhm:.2f} px")
    field(ax_c, 0.04, 0.095, 0.30, 0.036, "xcor peak:",  f"{last.xcor_peak:.2e}")

    section(ax_c, 0.048, "Tools")
    button(ax_c, 0.04, 0.005, 0.30, 0.034, "Estimate K…")
    button(ax_c, 0.36, 0.005, 0.30, 0.034, "Settings…")

    # Alert banner: not used (no alert in this run).
    ax_alert = fig.add_subplot(gs[3, :])
    ax_alert.set_facecolor("#3aa55d")
    ax_alert.set_xticks([]); ax_alert.set_yticks([])
    for s in ax_alert.spines.values():
        s.set_edgecolor("#1b5e20"); s.set_linewidth(0.7)
    ax_alert.text(
        0.012, 0.5,
        "OK   46 SUTRs replayed; last drift "
        f"({last.dx_px:+.2f}, {last.dy_px:+.2f}) px → wire frames sent.",
        transform=ax_alert.transAxes, va="center", ha="left",
        fontsize=10, color="#0e3a14",
    )

    # Time-series rows.
    ts_ranges = [
        ("dx, dy (px)",      [("dx_px", "#1f77b4", "dx"),
                              ("dy_px", "#d62728", "dy")]),
        ("FWHM (px)",        [("fwhm", "#2ca02c", None)]),
        ("flux (ADU)",       [("flux", "#9467bd", None)]),
        ("xcor peak",        [("xcor_peak", "#e377c2", None)]),
    ]
    idx = np.arange(len(history))
    frames = np.array([h.frame for h in history])
    for i, (label, traces) in enumerate(ts_ranges):
        ax = fig.add_subplot(gs[4 + i, :])
        ax.set_facecolor("#FFFFFF")
        for s in ax.spines.values():
            s.set_edgecolor("#999"); s.set_linewidth(0.5)
        for attr, col, name in traces:
            y = np.array([getattr(h, attr) if getattr(h, attr) is not None
                          else np.nan for h in history])
            ax.plot(idx, y, color=col, lw=1.0, marker="o", ms=3,
                     label=name)
        if any(name for _, _, name in traces):
            ax.legend(loc="upper right", fontsize=7.5, framealpha=0.85)
        # Frame boundary.
        for j in range(1, len(history)):
            if frames[j] != frames[j - 1]:
                ax.axvline(j - 0.5, color="#3aa55d", lw=0.6, alpha=0.5)
        ax.set_ylabel(label, fontsize=8.5)
        ax.tick_params(labelsize=7.5)
        ax.set_xticklabels([])
        if i == 0:
            ax.axhline(0, color="#888", lw=0.5, ls=":")

    # SNR histogram.
    ax_h = fig.add_subplot(gs[8, :])
    ax_h.set_facecolor("#FFFFFF")
    for s in ax_h.spines.values():
        s.set_edgecolor("#999"); s.set_linewidth(0.5)
    snr_flat = last_snr.ravel()
    snr_flat = snr_flat[snr_flat > 0]
    bins = np.linspace(0, np.percentile(snr_flat, 99.5), 80)
    ax_h.hist(snr_flat, bins=bins, color="#4c78a8", edgecolor="#274860",
              linewidth=0.4)
    mu = float(np.median(snr_flat))
    mad = float(np.median(np.abs(snr_flat - mu)))
    sigma = mad * 1.4826 if mad > 0 else float(np.std(snr_flat))
    bin_w = bins[1] - bins[0]
    x_g = np.linspace(bins[0], bins[-1], 600)
    g = (1.0 / (sigma * np.sqrt(2 * np.pi))) * np.exp(
        -0.5 * ((x_g - mu) / sigma) ** 2,
    )
    ax_h.plot(x_g, g * snr_flat.size * bin_w, color="#E63946", lw=1.6,
              label=fr"reference $\mathcal{{N}}(\mu={mu:.1f}, \sigma={sigma:.1f})$")
    ax_h.axvline(1.0, color="#888", lw=0.7, ls=":")
    ax_h.axvline(5.0, color="#888", lw=0.7, ls=":")
    ax_h.set_title(
        f"Per-pixel SNR histogram — current frame, science stamp "
        f"(real data: hen{last.frame:04d}_{last.sutr:03d}r)",
        fontsize=9, loc="left", pad=4,
    )
    ax_h.set_xlabel(r"per-pixel SNR  =  $\sqrt{\mathrm{signal\_DN}\,\cdot\,\mathrm{gain}}$",
                    fontsize=8.5)
    ax_h.set_ylabel("pixels", fontsize=8.5)
    ax_h.legend(loc="upper right", fontsize=8, framealpha=0.85)
    ax_h.tick_params(labelsize=7.5)

    fig.suptitle(
        "Henrietta autoguider — GUI mockup populated with REAL test/ data "
        "(46 SUTRs replayed through the live pipeline)",
        fontsize=12, fontweight="bold", x=0.045, y=0.992, ha="left",
        color="#222",
    )
    fig.savefig(OUT_PATH, dpi=130)


def main() -> int:
    print("Running simulator inline...")
    history, last_sub, template_img, last_snr = simulate()
    print(f"  {len(history)} samples; last frame "
          f"hen{history[-1].frame:04d}_{history[-1].sutr:03d}r")
    print("Rendering GUI snapshot...")
    render(history, last_sub, template_img, last_snr)
    print(f"  saved to {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
