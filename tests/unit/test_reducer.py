import numpy as np
import pytest

from henrietta_guider.core.reducer import Reducer
from henrietta_guider.core.template import Template
from henrietta_guider.core.types import Stamp


def _full_frame(value: float, ny: int = 2048, nx: int = 2048) -> np.ndarray:
    return np.full((ny, nx), value, dtype=np.float32)


def _stamp() -> Stamp:
    return Stamp(x_center=110, x_halfwidth=25, y_lo=600, y_hi=1980)


def _template(stamp: Stamp, frame_number: int = 1) -> Template:
    img = np.zeros(stamp.shape, dtype=np.float32)
    # Add a Gaussian trace down the middle so xcor has something to find.
    sigma = 1.5
    x_c = stamp.shape[1] // 2
    x = np.arange(stamp.shape[1])[None, :]
    img += 1000.0 * np.exp(-((x - x_c) ** 2) / (2 * sigma**2))
    good = np.ones(stamp.shape, dtype=bool)
    return Template(image=img, good=good, frame_number=frame_number, stamp=stamp)


@pytest.mark.unit
class TestReducer:
    def _make(self, K: int = 1, stride: int = 1) -> Reducer:
        good_full = np.ones((2048, 2048), dtype=bool)
        return Reducer(K=K, stride=stride, gain_e_per_dn=4.0, bpm_good=good_full)

    def test_first_sutr_no_guide_image_signal_snr_is_none(self):
        red = self._make()
        stamp, tmpl = _stamp(), _template(_stamp())
        # Frame 10, SUTR 1 (the reset read itself): no guide image yet,
        # and signal_DN = read - reset = 0 -> total_e <= 0 -> NULL per
        # spec §4.
        rows = red.reduce_sutr(
            frame_number=10,
            sutr_number=1,
            raw_read=_full_frame(50.0),
            stamps_and_templates=[(stamp, tmpl, 0)],
        )
        assert len(rows) == 1
        row = rows[0]
        assert row.dx_px is None  # no guide image yet
        assert row.signal_snr is None

    def test_second_sutr_emits_guide_image_and_xcor(self):
        red = self._make()
        stamp, tmpl = _stamp(), _template(_stamp())
        # SUTR 1: reset.
        red.reduce_sutr(
            frame_number=10,
            sutr_number=1,
            raw_read=_full_frame(50.0),
            stamps_and_templates=[(stamp, tmpl, 0)],
        )
        # SUTR 2: a slightly different read; FrameBuffer (K=1) emits.
        rows = red.reduce_sutr(
            frame_number=10,
            sutr_number=2,
            raw_read=_full_frame(50.0) + 1.0,  # +1 DN added everywhere
            stamps_and_templates=[(stamp, tmpl, 0)],
        )
        row = rows[0]
        assert row.dx_px is not None
        assert row.dy_px is not None
        assert row.xcor_peak_value is not None
        # Signal snr: signal_DN = (50+1) - 50 = 1 per pixel; in unmasked
        # stamp (51 x 1380 = 70380 px), total e- = 70380 * 1 * 4 = 281520;
        # snr = sqrt(281520) ~ 530.
        assert row.signal_snr == pytest.approx(530.0, rel=0.05)

    def test_frame_boundary_resets_reset_read_and_buffer(self):
        red = self._make()
        stamp, tmpl = _stamp(), _template(_stamp())
        # Two reads on frame 10:
        red.reduce_sutr(10, 1, _full_frame(50.0), stamps_and_templates=[(stamp, tmpl, 0)])
        red.reduce_sutr(10, 2, _full_frame(60.0), stamps_and_templates=[(stamp, tmpl, 0)])
        # New frame 11, SUTR 1 — buffer cleared, no guide image.
        rows = red.reduce_sutr(11, 1, _full_frame(80.0), stamps_and_templates=[(stamp, tmpl, 0)])
        assert rows[0].dx_px is None
        # signal_snr is relative to frame 11's reset (80.0 itself):
        # total_e <= 0 -> NULL per spec §4.
        assert rows[0].signal_snr is None

    def test_sanity_discard_returns_empty(self):
        red = self._make()
        stamp, tmpl = _stamp(), _template(_stamp())
        red.reduce_sutr(10, 1, _full_frame(50.0), stamps_and_templates=[(stamp, tmpl, 0)])
        red.reduce_sutr(10, 2, _full_frame(50.0), stamps_and_templates=[(stamp, tmpl, 0)])
        # Out-of-order SUTR: must return [] (discarded).
        rows = red.reduce_sutr(10, 1, _full_frame(50.0), stamps_and_templates=[(stamp, tmpl, 0)])
        assert rows == []

    def test_two_stamps_yields_two_rows(self):
        red = self._make()
        sci_stamp = Stamp(x_center=110, x_halfwidth=25, y_lo=600, y_hi=1980)
        cmp_stamp = Stamp(x_center=400, x_halfwidth=25, y_lo=600, y_hi=1980)
        sci_tmpl = _template(sci_stamp)
        cmp_tmpl = _template(cmp_stamp)
        red.reduce_sutr(
            10,
            1,
            _full_frame(50.0),
            stamps_and_templates=[(sci_stamp, sci_tmpl, 0), (cmp_stamp, cmp_tmpl, 1)],
        )
        rows = red.reduce_sutr(
            10,
            2,
            _full_frame(50.0) + 1.0,
            stamps_and_templates=[(sci_stamp, sci_tmpl, 0), (cmp_stamp, cmp_tmpl, 1)],
        )
        assert len(rows) == 2
        ids = {r.stamp_id for r in rows}
        assert ids == {0, 1}
