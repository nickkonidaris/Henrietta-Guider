"""Per-row local sky subtraction for stamps.

For each row of the stamp, the sky pedestal is the median of the outer
1/6 of pixels on **each** side (so 1/6 left + 1/6 right = 1/3 total
sampled per row), pooled into one value. Bad pixels (good == False in
the mask) are excluded from the median. The pedestal is subtracted
from every column in that row.

This matches ALGORITHM.md's sky step (`edge = sub.shape[1] // 6` then
both bands). It removes detector pedestal differences between reads,
sky-background gradients along the trace, and slow per-frame H2RG bias
drift — all of which would otherwise bias the cross-correlation peak
away from the structure that carries position information.
"""

from __future__ import annotations

import numpy as np


def subtract_local_sky(
    stamp: np.ndarray,
    good: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (sky-subtracted stamp, per-row sky values).

    `stamp` and `good` must have the same shape (ny, nx). `good` is the
    bad-pixel mask (True = good).
    """
    if stamp.shape != good.shape:
        raise ValueError(f"shape mismatch: {stamp.shape} vs {good.shape}")
    ny, nx = stamp.shape
    edge = max(1, nx // 6)
    # Build a boolean column-mask: True for the outer-1/6 columns on each side.
    edge_cols = np.zeros(nx, dtype=bool)
    edge_cols[:edge] = True
    edge_cols[-edge:] = True
    # Apply both column-mask and good-pixel mask for each row.
    masked = np.where(good & edge_cols[None, :], stamp, np.nan)
    per_row = np.nanmedian(masked, axis=1).astype(stamp.dtype)
    return stamp - per_row[:, None], per_row
