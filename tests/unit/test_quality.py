import pytest

from henrietta_guider.core.quality import OutOfFamilyDetector


@pytest.mark.unit
class TestOutOfFamilyDetector:
    def test_no_alert_during_warmup(self):
        det = OutOfFamilyDetector(window=20, warmup=10, sigma_threshold=5.0)
        # Push one obvious outlier on the very first frame: must not alert.
        verdict = det.update({"trace_flux_adu": 1e9})
        assert verdict.alerted is False
        assert verdict.warming_up is True

    def test_alerts_after_warmup_on_outlier(self):
        det = OutOfFamilyDetector(window=20, warmup=10, sigma_threshold=5.0)
        # Seed 10 in-family frames with flux ~ 1e5, FWHM ~ 3.0.
        for _ in range(10):
            det.update({"trace_flux_adu": 1.0e5, "trace_fwhm_x_px": 3.0})
        v = det.update({"trace_flux_adu": 1.0e3, "trace_fwhm_x_px": 3.0})
        assert v.alerted is True
        assert "trace_flux_adu" in v.offenders

    def test_auto_resume_after_n_in_family(self):
        det = OutOfFamilyDetector(
            window=20, warmup=10, sigma_threshold=5.0, auto_resume_in_family=3
        )
        for _ in range(10):
            det.update({"trace_flux_adu": 1.0e5})
        # An outlier alerts.
        v = det.update({"trace_flux_adu": 1.0e3})
        assert v.alerted is True
        # Three in-family frames — the third resumes.
        for _i in range(2):
            v = det.update({"trace_flux_adu": 1.0e5})
            assert v.alerted is False
            assert v.guiding is False  # still in alerted-pending-resume
        v = det.update({"trace_flux_adu": 1.0e5})
        assert v.alerted is False
        assert v.guiding is True

    def test_multiple_offenders_listed(self):
        det = OutOfFamilyDetector(window=20, warmup=5, sigma_threshold=5.0)
        for _ in range(5):
            det.update({"trace_flux_adu": 1e5, "trace_fwhm_x_px": 3.0, "sky_background_adu": 60.0})
        v = det.update(
            {
                "trace_flux_adu": 1e2,
                "trace_fwhm_x_px": 30.0,  # also outlier
                "sky_background_adu": 60.0,
            }
        )
        assert "trace_flux_adu" in v.offenders
        assert "trace_fwhm_x_px" in v.offenders
        assert "sky_background_adu" not in v.offenders

    def test_window_evicts_oldest(self):
        # Use noisy in-family values so MAD > 0 in both phases (a
        # zero-MAD buffer short-circuits the outlier check, which would
        # mask the eviction we're trying to test).
        det = OutOfFamilyDetector(window=5, warmup=3, sigma_threshold=5.0)
        # Phase 1: low-flux baseline with scatter.
        for v in [100.0, 105.0, 95.0, 102.0, 98.0]:
            det.update({"trace_flux_adu": v})
        # Phase 2: 5 new high-flux frames evict the lows; baseline
        # shifts to ~10000 with similar relative scatter.
        for v in [10000.0, 10100.0, 9900.0, 10050.0, 9950.0]:
            det.update({"trace_flux_adu": v})
        # Now 100.0 is the outlier relative to the new baseline.
        v = det.update({"trace_flux_adu": 100.0})
        assert v.alerted is True
