import math

import pytest

from henrietta_guider.core.target_switch import TargetSwitchDetector


@pytest.mark.unit
class TestTargetSwitchDetector:
    def test_first_call_no_alert(self):
        det = TargetSwitchDetector(threshold_arcsec=20.0)
        v = det.update(ra_deg=10.0, dec_deg=-30.0, object_name="STAR_A")
        assert v.severity == "none"

    def test_small_drift_no_alert(self):
        det = TargetSwitchDetector(threshold_arcsec=20.0)
        det.update(ra_deg=10.0, dec_deg=-30.0, object_name="A")
        # Move by 5" in RA: well under threshold.
        v = det.update(
            ra_deg=10.0 + 5.0 / 3600.0 / math.cos(math.radians(-30.0)),
            dec_deg=-30.0,
            object_name="A",
        )
        assert v.severity == "none"

    def test_pointing_jump_full_alert(self):
        det = TargetSwitchDetector(threshold_arcsec=20.0)
        det.update(ra_deg=10.0, dec_deg=-30.0, object_name="A")
        # Move by 30" in Dec.
        v = det.update(ra_deg=10.0, dec_deg=-30.0 + 30.0 / 3600.0, object_name="A")
        assert v.severity == "pointing"
        assert v.audible is True
        # Spec §4 specifies the exact spoken text.
        assert v.spoken_phrase == "target change possible"
        assert v.distance_arcsec == pytest.approx(30.0, abs=0.5)

    def test_object_only_change_soft_alert(self):
        det = TargetSwitchDetector(threshold_arcsec=20.0)
        det.update(ra_deg=10.0, dec_deg=-30.0, object_name="A")
        v = det.update(ra_deg=10.0, dec_deg=-30.0, object_name="B")
        assert v.severity == "object_only"
        assert v.audible is False  # tiny beep handled outside
        assert v.spoken_phrase is None

    def test_both_signals_pointing_wins(self):
        det = TargetSwitchDetector(threshold_arcsec=20.0)
        det.update(ra_deg=10.0, dec_deg=-30.0, object_name="A")
        v = det.update(ra_deg=10.0, dec_deg=-30.0 + 30.0 / 3600.0, object_name="B")
        assert v.severity == "pointing"

    def test_reset_clears_previous(self):
        # After reset() the next call should NOT compare against the old
        # frame (e.g., used after Save Reference clears running state).
        det = TargetSwitchDetector(threshold_arcsec=20.0)
        det.update(ra_deg=10.0, dec_deg=-30.0, object_name="A")
        det.reset()
        v = det.update(ra_deg=20.0, dec_deg=+45.0, object_name="Z")
        assert v.severity == "none"

    def test_costheta_correction(self):
        # At Dec=-60°, dRA in arcsec = dRA_deg * cos(-60°) * 3600.
        # 30" RA-on-sky at Dec=-60 corresponds to dRA_deg = 30/(0.5*3600).
        det = TargetSwitchDetector(threshold_arcsec=20.0)
        det.update(ra_deg=0.0, dec_deg=-60.0, object_name="A")
        v = det.update(ra_deg=30.0 / 3600.0 / 0.5, dec_deg=-60.0, object_name="A")
        assert v.severity == "pointing"
        assert v.distance_arcsec == pytest.approx(30.0, abs=0.5)
