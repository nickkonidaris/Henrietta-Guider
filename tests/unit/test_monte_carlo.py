import numpy as np
import pytest

from henrietta_guider.core.monte_carlo import estimate_k
from henrietta_guider.core.template import Template
from henrietta_guider.core.types import Stamp


@pytest.mark.unit
class TestEstimateK:
    def _template(self, ny=200, nx=51) -> Template:
        sigma = 1.5
        x_c = nx // 2
        x = np.arange(nx)[None, :]
        img = (1000.0 * np.exp(-((x - x_c) ** 2) / (2 * sigma**2)) * np.ones((ny, 1))).astype(
            np.float32
        )
        good = np.ones(img.shape, dtype=bool)
        return Template(image=img, good=good, frame_number=1, stamp=Stamp(50, 25, 0, ny))

    def test_returns_a_row_per_k(self):
        result = estimate_k(
            self._template(), gain_e_per_dn=4.0, read_noise_e=12.0, n_realisations=10, ks=(1, 2, 3)
        )
        assert [r.K for r in result.rows] == [1, 2, 3]
        assert all(r.rms_dx_px > 0 for r in result.rows)
        assert all(r.rms_dy_px > 0 for r in result.rows)

    def test_recommended_k_minimises_total_rms(self):
        result = estimate_k(
            self._template(),
            gain_e_per_dn=4.0,
            read_noise_e=12.0,
            n_realisations=20,
            ks=(1, 2, 3, 4, 5),
        )
        totals = [r.rms_dx_px**2 + r.rms_dy_px**2 for r in result.rows]
        assert result.recommended_K == result.rows[int(np.argmin(totals))].K
