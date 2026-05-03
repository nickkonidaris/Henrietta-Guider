"""Detector → sky transform.

The Henrietta detector pixel offsets (dx_px, dy_px) measured by the
2-D xcor pipeline must be converted to sky-frame offsets (RA, Dec) in
arcseconds before the controller acts on them. The TCS guide port
expects sky-frame offsets (see Wireformat.md).

Convention (Henrietta on Swope):

    PA = 0 → detector +X is along +Dec (North),
             detector +Y is along +RA  (East, "UP" in image display).
    PA increases from North toward East (standard astronomical PA).

So at PA = α:
    +X̂ on sky = cos α · N̂ + sin α · Ê
    +Ŷ on sky = -sin α · N̂ + cos α · Ê

A measured detector drift (dx, dy) projects to sky coordinates as:
    drift_Dec = dx · cos α  −  dy · sin α
    drift_RA  = dx · sin α  +  dy · cos α

Sign convention:

    sky offset = telescope correction = -(measured drift)

i.e. if the trace has drifted +1 px in detector X, the telescope must
move backwards in the corresponding sky direction to bring it back.
The minus sign lives here so the controller can work in the "drive
error to zero" convention.

Parity_x and parity_y encode the detector's handedness on the sky at
PA = 0: +1 means the detector axis aligns with the convention above;
-1 means it's flipped. These are pinned in config and verified against
an on-sky test offset during commissioning.
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

    Returns ``(dRA_arcsec, dDec_arcsec)`` — the telescope correction
    that cancels the drift. Equivalent to applying the parities, doing
    a 2-D rotation by PA, then negating both components ("correction =
    -drift"). Magnitude-preserving.
    """
    dx_arcsec = parity_x * dx_px * plate_scale_arcsec_per_px
    dy_arcsec = parity_y * dy_px * plate_scale_arcsec_per_px
    pa = math.radians(pa_deg)
    cos_pa, sin_pa = math.cos(pa), math.sin(pa)
    # drift_RA  = dx*sin α + dy*cos α   (+X→N, +Y→E at PA=0)
    # drift_Dec = dx*cos α − dy*sin α
    # correction = -drift, applied to both components.
    dra = -(dx_arcsec * sin_pa + dy_arcsec * cos_pa)
    ddec = -(dx_arcsec * cos_pa - dy_arcsec * sin_pa)
    return dra, ddec
