"""Template build from a slope-fit henNNNN.fits.

Steps:
  1. Open the FITS, read primary HDU as a 2-D float array.
  2. Extract the stamp [y_lo:y_hi, x_min:x_max).
  3. Apply the BPM (slice the master good-pixel mask).
  4. Subtract per-row local sky (sky.subtract_local_sky).
  5. Validate: enough unmasked pixels, non-zero variance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from astropy.io import fits

from .sky import subtract_local_sky
from .types import Stamp

_FNAME_RE = re.compile(r"^hen(\d{4})\.fits$")


class TemplateBuildError(Exception):
    """Raised by build_template on any failure mode."""


@dataclass(frozen=True)
class Template:
    """A built template: bg-subtracted, masked stamp + provenance."""

    image: np.ndarray
    good: np.ndarray
    frame_number: int
    stamp: Stamp


def build_template(
    path: str | Path,
    stamp: Stamp,
    good_full: np.ndarray,
    min_unmasked_fraction: float = 0.50,
    min_variance: float = 1e-6,
) -> Template:
    """Build a template from a henNNNN.fits slope-fit file."""
    p = Path(path).expanduser()
    m = _FNAME_RE.match(p.name)
    if m is None:
        raise TemplateBuildError(f"unparseable filename: {p.name!r} (expected henNNNN.fits)")
    frame_number = int(m.group(1))

    try:
        with fits.open(p) as hdul:
            full = np.asarray(hdul[0].data, dtype=np.float32)
    except FileNotFoundError as e:
        raise TemplateBuildError(f"failed to open {p}") from e
    except Exception as e:
        raise TemplateBuildError(f"failed to open {p}: {e}") from e

    stamp_img = full[stamp.y_lo : stamp.y_hi, stamp.x_min : stamp.x_max].copy()
    good_stamp = good_full[stamp.y_lo : stamp.y_hi, stamp.x_min : stamp.x_max].copy()

    n_unmasked = int(good_stamp.sum())
    n_total = good_stamp.size
    if n_unmasked < min_unmasked_fraction * n_total:
        raise TemplateBuildError(f"too few unmasked pixels: {n_unmasked} / {n_total}")

    sub, _ = subtract_local_sky(stamp_img, good_stamp)
    # Mask out bad pixels in the returned image (set to 0 so they don't
    # contribute to xcor sums).
    sub = np.where(good_stamp, sub, 0.0)
    if float(np.var(sub[good_stamp])) < min_variance:
        raise TemplateBuildError("zero variance after sky subtraction")

    return Template(image=sub, good=good_stamp, frame_number=frame_number, stamp=stamp)
