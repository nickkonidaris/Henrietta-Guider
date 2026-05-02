from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from henrietta_guider.core.template import (
    TemplateBuildError,
    build_template,
)
from henrietta_guider.core.types import Stamp


def _write_synthetic_henNNNN(
    path: Path, ny: int = 2048, nx: int = 2048, trace_x: int = 110
) -> None:
    img = np.full((ny, nx), 50.0, dtype=np.float32)
    # A bright trace in the science stamp region.
    img[600:1980, trace_x - 2 : trace_x + 3] += 2000.0
    fits.PrimaryHDU(img.astype(np.int16)).writeto(path, overwrite=True)


@pytest.mark.unit
class TestBuildTemplate:
    def _stamp(self) -> Stamp:
        return Stamp(x_center=110, x_halfwidth=25, y_lo=600, y_hi=1980)

    def test_happy_path(self, tmp_path: Path):
        p = tmp_path / "hen0042.fits"
        _write_synthetic_henNNNN(p)
        good = np.ones((2048, 2048), dtype=bool)
        tmpl = build_template(p, self._stamp(), good)
        assert tmpl.frame_number == 42
        # (y_hi-y_lo, 2*halfwidth + 1) per ALGORITHM.md.
        assert tmpl.image.shape == (1380, 51)
        # Sky should be subtracted: median of off-trace pixels ~ 0.
        offtrace = tmpl.image[:, :15]  # leftmost 15 cols (sky band)
        assert abs(np.median(offtrace)) < 5.0

    def test_filename_parsed(self, tmp_path: Path):
        p = tmp_path / "hen1764.fits"
        _write_synthetic_henNNNN(p)
        good = np.ones((2048, 2048), dtype=bool)
        tmpl = build_template(p, self._stamp(), good)
        assert tmpl.frame_number == 1764

    def test_missing_file_raises(self, tmp_path: Path):
        # Use a filename that PASSES the henNNNN.fits regex so the open
        # is what fails (not the regex check).
        with pytest.raises(TemplateBuildError, match="open"):
            build_template(
                tmp_path / "hen9999.fits", self._stamp(), np.ones((2048, 2048), dtype=bool)
            )

    def test_too_few_unmasked_raises(self, tmp_path: Path):
        p = tmp_path / "hen0001.fits"
        _write_synthetic_henNNNN(p)
        # Mark almost every pixel bad.
        good = np.zeros((2048, 2048), dtype=bool)
        good[1000, 110] = True  # one good pixel
        with pytest.raises(TemplateBuildError, match="unmasked"):
            build_template(p, self._stamp(), good)

    def test_zero_variance_raises(self, tmp_path: Path):
        p = tmp_path / "hen0002.fits"
        # Flat image -> after sky subtraction the stamp is zero -> no variance.
        fits.PrimaryHDU(np.full((2048, 2048), 50.0, dtype=np.int16)).writeto(p, overwrite=True)
        good = np.ones((2048, 2048), dtype=bool)
        with pytest.raises(TemplateBuildError, match="variance"):
            build_template(p, self._stamp(), good)

    def test_unparseable_filename_raises(self, tmp_path: Path):
        p = tmp_path / "weird.fits"
        _write_synthetic_henNNNN(p)
        good = np.ones((2048, 2048), dtype=bool)
        with pytest.raises(TemplateBuildError, match="filename"):
            build_template(p, self._stamp(), good)
