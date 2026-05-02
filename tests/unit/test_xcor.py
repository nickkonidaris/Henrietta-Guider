import numpy as np
import pytest

from henrietta_guider.core.xcor import xcor_2d


def _gaussian_trace(
    ny: int = 200, nx: int = 50, x_center: float = 25.0, fwhm_px: float = 3.5
) -> np.ndarray:
    """Synthetic stamp: a Gaussian trace running down Y."""
    sigma = fwhm_px / 2.355
    x = np.arange(nx)[None, :]
    profile = np.exp(-((x - x_center) ** 2) / (2 * sigma**2))
    # Y modulation: a slow continuum + a couple of "absorption" dips.
    cont = 1.0 + 0.10 * np.sin(np.linspace(0, 6.0, ny))
    cont -= 0.40 * np.exp(-((np.arange(ny) - 60) ** 2) / 8.0)
    cont -= 0.30 * np.exp(-((np.arange(ny) - 140) ** 2) / 12.0)
    return (profile * cont[:, None] * 1000.0).astype(np.float32)


def _shift_image(img: np.ndarray, dx: int, dy: int) -> np.ndarray:
    """Integer-shift (no interpolation; used only for integer-truth tests)."""
    return np.roll(np.roll(img, dy, axis=0), dx, axis=1)


@pytest.mark.unit
class TestXcor2D:
    def test_zero_shift_returns_zero(self):
        template = _gaussian_trace()
        data = template.copy()
        result = xcor_2d(data, template, search=12)
        assert result.dx_px == pytest.approx(0.0, abs=0.05)
        assert result.dy_px == pytest.approx(0.0, abs=0.05)

    def test_integer_x_shift_recovered(self):
        template = _gaussian_trace()
        data = _shift_image(template, dx=3, dy=0)
        result = xcor_2d(data, template, search=12)
        assert result.dx_px == pytest.approx(3.0, abs=0.05)
        assert result.dy_px == pytest.approx(0.0, abs=0.05)

    def test_integer_y_shift_recovered(self):
        template = _gaussian_trace()
        data = _shift_image(template, dx=0, dy=-5)
        result = xcor_2d(data, template, search=12)
        assert result.dx_px == pytest.approx(0.0, abs=0.05)
        assert result.dy_px == pytest.approx(-5.0, abs=0.05)

    def test_subpixel_x_shift_recovered(self):
        # 0.4 px X-shift via cubic spline. Tolerance is 0.10 px to
        # accommodate the combined bias of (cubic interpolation ~ a few
        # 0.01 px) + (parabolic-peak fit on a ~Gaussian xcor surface ~
        # a few 0.01 px). A real on-sky test will tighten this once we
        # know the actual point-spread function.
        template = _gaussian_trace()
        from scipy.ndimage import shift as scipy_shift

        data = scipy_shift(template, (0.0, 0.4), order=3, mode="reflect")
        result = xcor_2d(data, template, search=12)
        assert result.dx_px == pytest.approx(0.4, abs=0.10)
        assert result.dy_px == pytest.approx(0.0, abs=0.10)

    def test_subpixel_y_shift_recovered(self):
        from scipy.ndimage import shift as scipy_shift

        template = _gaussian_trace()
        data = scipy_shift(template, (0.25, 0.0), order=3, mode="reflect")
        result = xcor_2d(data, template, search=12)
        assert result.dy_px == pytest.approx(0.25, abs=0.10)

    def test_curvature_positive_at_peak(self):
        template = _gaussian_trace()
        data = template.copy()
        result = xcor_2d(data, template, search=8)
        # Parabolic curvature at the peak is (a - 2b + c) where b is the
        # max. For a Gaussian-like correlation surface this is negative
        # (concave down) — we record the negative-magnitude value as a
        # precision proxy. Magnitude > 0 is what the GUI displays.
        assert result.curvature_x < 0.0
        assert result.curvature_y < 0.0

    def test_search_window_too_small_clips_peak(self):
        # If true shift exceeds the search radius, the integer peak
        # lands at the edge — peak_value still positive, but the
        # parabolic fit may be unreliable. The function should not
        # crash; it should return a peak at the edge.
        from scipy.ndimage import shift as scipy_shift

        template = _gaussian_trace()
        data = scipy_shift(template, (0.0, 15.0), order=3, mode="reflect")
        result = xcor_2d(data, template, search=5)
        # Just verify no crash; the recovered shift will be roughly +5
        # (clipped) or wraparound — implementation-defined.
        assert result.peak_value > 0.0
