"""Bad-pixel mask loader.

The Henrietta BPM (bpm_25apr2026.fits) is a 7-HDU MEF:

    HDU 0 (primary)  master good-pixel map  (1 = good, 0 = bad)
    HDU 1 COVERAGE   1 = illuminated science region
    HDU 2 DEAD       1 = dead pixel
    HDU 3 HOT        1 = hot pixel
    HDU 4 NOISY      1 = noisy in light
    HDU 5 NOISY_DARK 1 = noisy in dark
    HDU 6 REF_PIX    1 = H2RG reference pixel

The autoguider only reads HDU 0. The other HDUs are diagnostic
categories that are already folded into the master.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from astropy.io import fits


def load_bpm(path: str | Path) -> np.ndarray:
    """Load the master good-pixel mask as a boolean numpy array.

    Returns an array with the same shape as the science detector,
    where True == good (master HDU 0 == 1) and False == bad (== 0).
    """
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(p)
    with fits.open(p) as hdul:
        master = hdul[0].data
    return master.astype(bool)
