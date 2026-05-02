"""2-D cross-correlation with parabolic sub-pixel peak (ALGORITHM.md).

For each candidate (dx, dy) in a +/- search window, compute:
    C(dx, dy) = sum_y sum_x  T(x, y) * D(x + dx, y + dy)
The integer peak is at argmax(C). A parabolic fit to the three
correlation values around the peak in each axis independently gives
sub-pixel refinement:
    sub = 0.5 * (a - c) / (a - 2b + c)
The curvature (a - 2b + c) is recorded as a precision proxy.
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
    search: int = 12,
) -> XcorResult:
    """Brute-force 2-D xcor with parabolic sub-pixel peak.

    Sign convention: returns the (dx, dy) such that
    ``data ~= np.roll(template, (dy, dx))``. So a positive dx means the
    data is shifted to the +X direction relative to the template, and
    the integer-shift unit test ``data = np.roll(template, dx=+3, axis=1)``
    recovers ``dx_px ~= +3``. Downstream geometry.py negates this to
    produce the telescope correction.
    """
    if data.shape != template.shape:
        raise ValueError(f"shape mismatch: {data.shape} vs {template.shape}")

    ny, nx = template.shape
    n_dx = 2 * search + 1
    n_dy = 2 * search + 1
    C = np.zeros((n_dy, n_dx), dtype=np.float64)

    # For each candidate (dx, dy), roll D by (-dy, -dx) so that
    # D'[y, x] = D[y + dy, x + dx], then sum T * D'. We use cyclic
    # np.roll (rather than slicing the overlap) because for small
    # search windows (+/- 12) and a ~70k-pixel stamp, the wraparound
    # bias is negligible relative to the central correlation peak,
    # and the simpler implementation avoids overlap-area effects that
    # otherwise bias the peak toward (0, 0) on positive-valued stamps.
    for iy, dy in enumerate(range(-search, search + 1)):
        for ix, dx in enumerate(range(-search, search + 1)):
            rolled = np.roll(np.roll(data, -dy, axis=0), -dx, axis=1)
            C[iy, ix] = float(np.sum(template * rolled))

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
