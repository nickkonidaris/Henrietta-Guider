import numpy as np
import pytest

from henrietta_guider.core.xcor import xcor_2d


def _gaussian_trace(
    ny: int = 200,
    nx: int = 50,
    x_center: float = 25.0,
    fwhm_px: float = 3.5,
    rng_seed: int = 0,
) -> np.ndarray:
    """Synthetic stamp: a Gaussian trace running down Y with strong
    Y structure (so Y-direction correlation is well-localised).

    The base Gaussian has a constant X-profile every row; add high-
    frequency Y modulation (per-row noise) so the Y-autocorrelation
    drops sharply with offset — otherwise sliced overlap can't
    localise Y on smooth-continuum data.

    Mean-subtracted before return — the production caller is expected
    to sky-subtract too, so this matches the algorithm contract.
    """
    rng = np.random.default_rng(rng_seed)
    sigma = fwhm_px / 2.355
    x = np.arange(nx)[None, :]
    profile = np.exp(-((x - x_center) ** 2) / (2 * sigma**2))
    # Strong, bias-free Y modulation: per-row noise + a couple of dips.
    cont = 1.0 + 0.30 * rng.standard_normal(ny)
    cont -= 0.40 * np.exp(-((np.arange(ny) - 60) ** 2) / 8.0)
    cont -= 0.30 * np.exp(-((np.arange(ny) - 140) ** 2) / 12.0)
    img = (profile * cont[:, None] * 1000.0).astype(np.float32)
    return img - img.mean()


def _shift_image_zero_pad(img: np.ndarray, dx: int, dy: int) -> np.ndarray:
    """Integer-shift with zero-padding (NOT cyclic, unlike np.roll).

    Pixels that fall off any edge are dropped; the gaps left at the
    opposite edge are filled with zeros (which on a mean-subtracted
    image are the natural "no signal" value).
    """
    out = np.zeros_like(img)
    src_y_lo = max(0, -dy)
    src_y_hi = img.shape[0] - max(0, dy)
    src_x_lo = max(0, -dx)
    src_x_hi = img.shape[1] - max(0, dx)
    dst_y_lo = max(0, dy)
    dst_y_hi = dst_y_lo + (src_y_hi - src_y_lo)
    dst_x_lo = max(0, dx)
    dst_x_hi = dst_x_lo + (src_x_hi - src_x_lo)
    out[dst_y_lo:dst_y_hi, dst_x_lo:dst_x_hi] = img[src_y_lo:src_y_hi, src_x_lo:src_x_hi]
    return out


@pytest.mark.unit
class TestXcor2D:
    def test_zero_shift_returns_zero(self):
        template = _gaussian_trace()
        data = template.copy()
        result = xcor_2d(data, template, search=5)
        assert result.dx_px == pytest.approx(0.0, abs=0.05)
        assert result.dy_px == pytest.approx(0.0, abs=0.05)

    def test_integer_x_shift_recovered(self):
        template = _gaussian_trace()
        data = _shift_image_zero_pad(template, dx=2, dy=0)
        result = xcor_2d(data, template, search=5)
        assert result.dx_px == pytest.approx(2.0, abs=0.05)
        assert result.dy_px == pytest.approx(0.0, abs=0.10)

    def test_integer_y_shift_recovered(self):
        template = _gaussian_trace()
        data = _shift_image_zero_pad(template, dx=0, dy=-2)
        result = xcor_2d(data, template, search=5)
        assert result.dx_px == pytest.approx(0.0, abs=0.10)
        assert result.dy_px == pytest.approx(-2.0, abs=0.10)

    def test_subpixel_x_shift_recovered(self):
        from scipy.ndimage import shift as scipy_shift

        template = _gaussian_trace()
        data = scipy_shift(template, (0.0, 0.4), order=3, mode="constant", cval=0.0)
        result = xcor_2d(data, template, search=3)
        assert result.dx_px == pytest.approx(0.4, abs=0.10)
        assert result.dy_px == pytest.approx(0.0, abs=0.15)

    def test_subpixel_y_shift_recovered(self):
        from scipy.ndimage import shift as scipy_shift

        template = _gaussian_trace()
        data = scipy_shift(template, (0.25, 0.0), order=3, mode="constant", cval=0.0)
        result = xcor_2d(data, template, search=3)
        assert result.dy_px == pytest.approx(0.25, abs=0.15)

    def test_curvature_negative_at_peak(self):
        template = _gaussian_trace()
        data = template.copy()
        result = xcor_2d(data, template, search=3)
        # Concave-down peak -> curvature (a - 2b + c) < 0 along both axes.
        assert result.curvature_x < 0.0
        assert result.curvature_y < 0.0

    def test_default_search_radius_is_four(self):
        # Defaults to 4 because that's close to the guider's per-command
        # range (±2.5" ÷ ~0.7"/px ≈ ±3.6 px); wider search is wasted work
        # since we couldn't correct it in one frame anyway.
        import inspect

        from henrietta_guider.core import xcor as xcor_module

        sig = inspect.signature(xcor_module.xcor_2d)
        assert sig.parameters["search"].default == 4
