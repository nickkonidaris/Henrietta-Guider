import math

import pytest

from henrietta_guider.core.geometry import detector_to_sky


@pytest.mark.unit
class TestDetectorToSky:
    """detector_to_sky returns the *correction* (telescope offset that
    cancels a measured detector-frame drift). At PA=0 with parities
    +1/+1, a +1 px drift in detector X corresponds to +1 px of trace
    motion toward east on the sky, so the correction is -plate arcsec
    in RA. Same handedness for Y/Dec: +1 px drift -> -plate arcsec
    correction in Dec. The function's overall sign is "correction =
    -drift" applied uniformly to both axes, with a 2-D rotation by PA
    in between.
    """

    PLATE = 0.435  # arcsec/px (placeholder; real value from William)

    def test_zero_pa_zero_offset(self):
        ra, dec = detector_to_sky(0.0, 0.0, self.PLATE, 0.0, +1, +1)
        assert ra == pytest.approx(0.0)
        assert dec == pytest.approx(0.0)

    def test_pa_zero_x_maps_to_negative_ra_correction(self):
        # +1 px drift in detector X at PA=0 with parity_x=+1 corresponds
        # to the trace having moved +RA on the sky. Correction = -drift,
        # so the returned dRA is -plate.
        ra, dec = detector_to_sky(1.0, 0.0, self.PLATE, 0.0, +1, +1)
        assert ra == pytest.approx(-self.PLATE)
        assert dec == pytest.approx(0.0, abs=1e-12)

    def test_pa_zero_y_maps_to_negative_dec_correction(self):
        # +1 px drift in detector Y at PA=0 with parity_y=+1 corresponds
        # to the trace having moved +Dec on the sky. Correction = -drift.
        ra, dec = detector_to_sky(0.0, 1.0, self.PLATE, 0.0, +1, +1)
        assert ra == pytest.approx(0.0, abs=1e-12)
        assert dec == pytest.approx(-self.PLATE)

    def test_pa_90_x_drift_becomes_dec_correction(self):
        # At PA=90, detector +Y points east (+RA) and detector +X points
        # south (-Dec). A +1 px drift in detector X is therefore -Dec
        # drift on the sky -> correction is +plate in Dec.
        ra, dec = detector_to_sky(1.0, 0.0, self.PLATE, 90.0, +1, +1)
        assert ra == pytest.approx(0.0, abs=1e-12)
        assert dec == pytest.approx(+self.PLATE)

    def test_parity_flip_x(self):
        # Flipping parity_x flips the RA contribution.
        ra_p, _ = detector_to_sky(1.0, 0.0, self.PLATE, 0.0, +1, +1)
        ra_n, _ = detector_to_sky(1.0, 0.0, self.PLATE, 0.0, -1, +1)
        assert ra_n == pytest.approx(-ra_p)

    def test_parity_flip_y(self):
        # And similarly for Dec via parity_y.
        _, dec_p = detector_to_sky(0.0, 1.0, self.PLATE, 0.0, +1, +1)
        _, dec_n = detector_to_sky(0.0, 1.0, self.PLATE, 0.0, +1, -1)
        assert dec_n == pytest.approx(-dec_p)

    def test_pa_45_diagonal(self):
        # PA=45 with dx=1, dy=0: drift = (cos45, -sin45) * PLATE,
        # correction = -drift = (-cos45, +sin45) * PLATE.
        # Pin the rotation direction explicitly so a future formula
        # tweak that swaps cos/sin gets caught.
        ra, dec = detector_to_sky(1.0, 0.0, self.PLATE, 45.0, +1, +1)
        s = math.sqrt(0.5)
        assert ra == pytest.approx(-self.PLATE * s)
        assert dec == pytest.approx(+self.PLATE * s)

    def test_full_pa_sweep_preserves_magnitude(self):
        # The total (RA, Dec) magnitude must equal sqrt(dx^2 + dy^2) * plate
        # for any PA / parity combo (a rotation+sign-flip preserves L2).
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
