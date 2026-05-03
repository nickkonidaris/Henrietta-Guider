import math

import pytest

from henrietta_guider.core.geometry import detector_to_sky


@pytest.mark.unit
class TestDetectorToSky:
    """detector_to_sky returns the *correction* (telescope offset that
    cancels a measured detector-frame drift).

    Convention (Henrietta on Swope): at PA=0, detector +X is along
    +Dec (North) and detector +Y is along +RA (East, "UP" in image
    display). Correction = -drift, applied uniformly to both axes
    after the parities and a 2-D rotation by PA.
    """

    PLATE = 0.435  # arcsec/px

    def test_zero_pa_zero_offset(self):
        ra, dec = detector_to_sky(0.0, 0.0, self.PLATE, 0.0, +1, +1)
        assert ra == pytest.approx(0.0)
        assert dec == pytest.approx(0.0)

    def test_pa_zero_x_maps_to_negative_dec_correction(self):
        # At PA=0 with parity_x=+1, +1 px in detector X = +1 px of trace
        # motion toward +Dec on the sky. Correction = -drift, so dDec=-PLATE.
        ra, dec = detector_to_sky(1.0, 0.0, self.PLATE, 0.0, +1, +1)
        assert ra == pytest.approx(0.0, abs=1e-12)
        assert dec == pytest.approx(-self.PLATE)

    def test_pa_zero_y_maps_to_negative_ra_correction(self):
        # At PA=0 with parity_y=+1, +1 px in detector Y = +1 px of trace
        # motion toward +RA (East) on the sky. dRA=-PLATE.
        ra, dec = detector_to_sky(0.0, 1.0, self.PLATE, 0.0, +1, +1)
        assert ra == pytest.approx(-self.PLATE)
        assert dec == pytest.approx(0.0, abs=1e-12)

    def test_pa_90_x_drift_becomes_ra_correction(self):
        # At PA=90 the camera has rotated North→East by 90°, so
        # detector +X is now aligned with +RA (East). +1 px in X is
        # therefore +RA drift; correction = -PLATE in RA.
        ra, dec = detector_to_sky(1.0, 0.0, self.PLATE, 90.0, +1, +1)
        assert ra == pytest.approx(-self.PLATE)
        assert dec == pytest.approx(0.0, abs=1e-12)

    def test_parity_flip_x_inverts_dec(self):
        # At PA=0 the X axis maps to Dec, so flipping parity_x flips Dec.
        _, dec_p = detector_to_sky(1.0, 0.0, self.PLATE, 0.0, +1, +1)
        _, dec_n = detector_to_sky(1.0, 0.0, self.PLATE, 0.0, -1, +1)
        assert dec_n == pytest.approx(-dec_p)

    def test_parity_flip_y_inverts_ra(self):
        # At PA=0 the Y axis maps to RA, so flipping parity_y flips RA.
        ra_p, _ = detector_to_sky(0.0, 1.0, self.PLATE, 0.0, +1, +1)
        ra_n, _ = detector_to_sky(0.0, 1.0, self.PLATE, 0.0, +1, -1)
        assert ra_n == pytest.approx(-ra_p)

    def test_pa_45_diagonal(self):
        # PA=45, dx=1, dy=0:
        #   drift_RA  = sin(45)*PLATE = +√½·PLATE
        #   drift_Dec = cos(45)*PLATE = +√½·PLATE
        # correction = -drift on both axes.
        ra, dec = detector_to_sky(1.0, 0.0, self.PLATE, 45.0, +1, +1)
        s = math.sqrt(0.5)
        assert ra == pytest.approx(-self.PLATE * s)
        assert dec == pytest.approx(-self.PLATE * s)

    def test_full_pa_sweep_preserves_magnitude(self):
        # The total (RA, Dec) magnitude must equal sqrt(dx²+dy²) * plate
        # for any PA / parity combo (rotation+sign-flip preserves L2).
        for pa_deg in (0, 17, 33, 90, 180, 271, 359):
            for px, py in (-1, -1), (+1, +1), (+3, -2):
                for parx in (+1, -1):
                    for pary in (+1, -1):
                        ra, dec = detector_to_sky(
                            float(px),
                            float(py),
                            self.PLATE,
                            float(pa_deg),
                            parx,
                            pary,
                        )
                        expected = self.PLATE * math.hypot(px, py)
                        assert math.hypot(ra, dec) == pytest.approx(expected, abs=1e-9)
