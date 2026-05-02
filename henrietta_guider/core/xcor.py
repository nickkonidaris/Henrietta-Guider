"""2-D cross-correlation with parabolic sub-pixel peak (ALGORITHM.md).

For each candidate (dx, dy) in a +/- search window, compute the SLICED
overlap correlation (no wraparound):

    C(dx, dy) = sum over the OVERLAP REGION of T(x, y) * D(x + dx, y + dy)

The integer peak is at argmax(C). A parabolic fit to the three
correlation values around the peak in each axis independently gives
sub-pixel refinement:

    sub = 0.5 * (a - c) / (a - 2b + c)

The curvature (a - 2b + c) is recorded as a precision proxy.

Caller contract: pass mean-zero (sky-subtracted) inputs. Otherwise the
overlap-area effect (smaller area at non-zero shifts) dominates the
correlation and the peak gets pinned at (0, 0). The production reducer
runs `subtract_local_sky` before calling this.

The default search radius (3 px) reflects realistic Henrietta motion:
plate scale ~0.7\"/px, typical drifts << 1 pixel; 3 px is generous. If
a real on-sky run shows larger excursions, raise via config rather than
defaulting wider.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class XcorResult:
    dx_px: float
    dy_px: float
    peak_value: float
    curvature_x: float
    curvature_y: float


def xcor_2d(
    data: np.ndarray,
    template: np.ndarray,
    search: int = 3,
) -> XcorResult:
    """Brute-force 2-D xcor with parabolic sub-pixel peak.

    Sign convention: returns the (dx, dy) such that
    ``data ~ shift(template, (dy, dx))``. So a positive dx means the
    data is shifted in the +X direction relative to the template.
    Downstream geometry.py negates this to produce the telescope
    correction.
    """
    if data.shape != template.shape:
        raise ValueError(f"shape mismatch: {data.shape} vs {template.shape}")

    ny, nx = template.shape
    n_dx = 2 * search + 1
    n_dy = 2 * search + 1
    C = np.zeros((n_dy, n_dx), dtype=np.float64)

    for iy, dy in enumerate(range(-search, search + 1)):
        for ix, dx in enumerate(range(-search, search + 1)):
            y_lo_t = max(0, -dy)
            y_hi_t = ny - max(0, dy)
            x_lo_t = max(0, -dx)
            x_hi_t = nx - max(0, dx)
            t_view = template[y_lo_t:y_hi_t, x_lo_t:x_hi_t]
            d_view = data[
                y_lo_t + dy : y_hi_t + dy,
                x_lo_t + dx : x_hi_t + dx,
            ]
            C[iy, ix] = float(np.sum(t_view * d_view))

    iy_peak, ix_peak = np.unravel_index(int(np.argmax(C)), C.shape)
    peak_value = float(C[iy_peak, ix_peak])

    sub_x, curv_x = _parabolic_sub(C, iy_peak, ix_peak, axis="x")
    sub_y, curv_y = _parabolic_sub(C, iy_peak, ix_peak, axis="y")

    dx = (ix_peak - search) + sub_x
    dy = (iy_peak - search) + sub_y
    return XcorResult(
        dx_px=dx,
        dy_px=dy,
        peak_value=peak_value,
        curvature_x=curv_x,
        curvature_y=curv_y,
    )


def _parabolic_sub(
    C: np.ndarray,
    iy: int,
    ix: int,
    axis: str,
) -> tuple[float, float]:
    """Sub-pixel refinement via parabolic fit on the 3 values around the peak.

    Returns (sub-pixel offset, curvature). When the peak sits on the
    edge of the search window, returns (0.0, 0.0) — the integer peak
    is the best we can do.
    """
    ny, nx = C.shape
    if axis == "x":
        if ix == 0 or ix == nx - 1:
            return 0.0, 0.0
        a, b, c = C[iy, ix - 1], C[iy, ix], C[iy, ix + 1]
    else:
        if iy == 0 or iy == ny - 1:
            return 0.0, 0.0
        a, b, c = C[iy - 1, ix], C[iy, ix], C[iy + 1, ix]
    denom = a - 2.0 * b + c
    if denom == 0.0:
        return 0.0, 0.0
    return 0.5 * (a - c) / denom, denom
