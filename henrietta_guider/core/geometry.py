"""Detector → sky transform.

The Henrietta detector pixel offsets (dx_px, dy_px) measured by the
2-D xcor pipeline must be converted to sky-frame offsets (RA, Dec) in
arcseconds before the controller acts on them. The TCS guide port
expects sky-frame offsets (see Wireformat.md).

Sign convention (TBC with William; see Q14 in Questions-for-William.md):

    sky offset = telescope correction = -(measured drift)

In other words, if the trace has drifted +1 px in detector X, the
telescope must move -1 px in detector X (from its current pointing) to
bring the trace back. The minus sign lives here so the controller can
work in the "drive error to zero" convention.

Parity_x and parity_y encode the detector's handedness on the sky at
PA = 0: e.g. parity_x = +1 means +X-detector aligns with +RA-sky at
PA = 0; parity_x = -1 means it aligns with -RA. These are pinned in
config and verified against an on-sky test offset during commissioning.
"""

from __future__ import annotations

import math


def detector_to_sky(
    dx_px: float,
    dy_px: float,
    plate_scale_arcsec_per_px: float,
    pa_deg: float,
    parity_x: int,
    parity_y: int,
) -> tuple[float, float]:
    """Convert a measured detector pixel drift to a sky-frame correction.

    Returns (dRA_arcsec, dDec_arcsec) — the telescope correction that
    cancels the drift. Equivalent to applying the parities, doing a 2-D
    rotation by PA, then negating both components ("correction =
    -drift").
    """
    dx_arcsec = parity_x * dx_px * plate_scale_arcsec_per_px
    dy_arcsec = parity_y * dy_px * plate_scale_arcsec_per_px
    pa = math.radians(pa_deg)
    cos_pa, sin_pa = math.cos(pa), math.sin(pa)
    # Convention: detector +Y is east of north by PA, so the drift in
    # (RA, Dec) from a detector pixel offset (dx, dy) is:
    #     drift_RA  = dx*cos_pa + dy*sin_pa
    #     drift_Dec = dy*cos_pa - dx*sin_pa
    # Correction = -drift; both components flipped together so the
    # transform stays magnitude-preserving (rotation × -1).
    dra = -(dx_arcsec * cos_pa + dy_arcsec * sin_pa)
    ddec = -(dy_arcsec * cos_pa - dx_arcsec * sin_pa)
    return dra, ddec
