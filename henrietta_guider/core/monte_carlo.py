"""Estimate K Monte Carlo simulator (spec §9 Estimate K tool).

For each candidate K, draw n_realisations noisy K-window difference
images by adding Poisson shot noise + read noise to the template, run
the same xcor pipeline used for live guiding, and report the RMS of
the recovered (dx_px, dy_px). Recommends the smallest K that
minimises the total RMS.

Caller invokes from a short-lived worker thread (spec §9 "Threading
rules" for Estimate K) so live guiding is unaffected.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .template import Template
from .xcor import xcor_2d


@dataclass(frozen=True)
class EstimateKRow:
    K: int
    rms_dx_px: float
    rms_dy_px: float


@dataclass(frozen=True)
class EstimateKResult:
    rows: list[EstimateKRow]
    recommended_K: int


def estimate_k(
    template: Template,
    gain_e_per_dn: float,
    read_noise_e: float,
    n_realisations: int = 50,
    ks: tuple[int, ...] = (1, 2, 3, 4, 5),
    seed: int | None = None,
) -> EstimateKResult:
    rng = np.random.default_rng(seed)
    rows: list[EstimateKRow] = []
    for K in ks:
        dxs: list[float] = []
        dys: list[float] = []
        for _ in range(n_realisations):
            signal_e = np.clip(template.image, 0, None) * gain_e_per_dn
            noisy_e = rng.poisson(signal_e).astype(np.float32)
            rn_per_pix = read_noise_e * np.sqrt(2.0 / K)
            noisy = noisy_e / gain_e_per_dn + rng.normal(
                0.0,
                rn_per_pix / gain_e_per_dn,
                size=template.image.shape,
            ).astype(np.float32)
            xc = xcor_2d(noisy, template.image, search=12)
            dxs.append(xc.dx_px)
            dys.append(xc.dy_px)
        rms_dx = float(np.sqrt(np.mean(np.array(dxs) ** 2)))
        rms_dy = float(np.sqrt(np.mean(np.array(dys) ** 2)))
        rows.append(EstimateKRow(K=K, rms_dx_px=rms_dx, rms_dy_px=rms_dy))

    totals = [r.rms_dx_px**2 + r.rms_dy_px**2 for r in rows]
    best = rows[int(np.argmin(totals))].K
    return EstimateKResult(rows=rows, recommended_K=best)
