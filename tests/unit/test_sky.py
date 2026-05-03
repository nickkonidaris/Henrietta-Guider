import numpy as np
import pytest

from henrietta_guider.core.sky import subtract_local_sky


def _stamp_with_constant_sky_and_trace(
    ny: int, nx: int, sky_level: float = 50.0, trace_amplitude: float = 1000.0
) -> np.ndarray:
    img = np.full((ny, nx), sky_level, dtype=np.float32)
    img[:, nx // 2] += trace_amplitude  # narrow trace down the middle
    return img


@pytest.mark.unit
class TestSubtractLocalSky:
    def test_uniform_sky_removed_to_zero(self):
        ny, nx = 100, 60
        img = _stamp_with_constant_sky_and_trace(ny, nx, sky_level=42.0)
        good = np.ones((ny, nx), dtype=bool)
        sub, per_row = subtract_local_sky(img, good)
        # Outside the trace column, pixels should now be ~0.
        flat_offrow = np.delete(sub, nx // 2, axis=1)
        np.testing.assert_allclose(flat_offrow.mean(), 0.0, atol=1e-6)
        # per-row sky is the constant 42 for every row.
        np.testing.assert_allclose(per_row, 42.0)

    def test_per_row_gradient_followed(self):
        # Sky has a row-dependent pedestal: row 0 -> 10, row N-1 -> 100.
        ny, nx = 50, 60
        sky_per_row = np.linspace(10.0, 100.0, ny, dtype=np.float32)
        img = np.repeat(sky_per_row[:, None], nx, axis=1)
        good = np.ones_like(img, dtype=bool)
        sub, per_row = subtract_local_sky(img, good)
        np.testing.assert_allclose(sub, 0.0, atol=1e-6)
        np.testing.assert_allclose(per_row, sky_per_row, atol=1e-6)

    def test_bad_pixels_excluded_from_sky(self):
        ny, nx = 10, 60
        img = np.full((ny, nx), 50.0, dtype=np.float32)
        # Drop a wild outlier into the left sky band: would skew the
        # median if it were included.
        img[5, 2] = 99999.0
        good = np.ones_like(img, dtype=bool)
        good[5, 2] = False
        sub, per_row = subtract_local_sky(img, good)
        # Median of all-50 outer-1/6 (after masking the wild pixel) -> 50.
        assert per_row[5] == pytest.approx(50.0)

    def test_outer_one_sixth_is_used(self):
        # Width 60 -> outer 1/6 = 10 pixels each side. Put a poison
        # pixel JUST INSIDE the boundary (column 10), which should NOT
        # affect the row median.
        ny, nx = 5, 60
        img = np.full((ny, nx), 50.0, dtype=np.float32)
        img[:, 10] = 9999.0  # column 10 is OUTSIDE the outer 1/6 (which
        # spans cols 0..9 and 50..59)
        good = np.ones_like(img, dtype=bool)
        _, per_row = subtract_local_sky(img, good)
        np.testing.assert_allclose(per_row, 50.0)
