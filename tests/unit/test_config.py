from pathlib import Path

import pytest

from henrietta_guider.core.config import Config, load_config, save_config


@pytest.mark.unit
class TestConfigDefaults:
    def test_loop_defaults(self):
        c = Config()
        assert c.loop.Kp_ra == pytest.approx(0.5)
        assert c.loop.Kp_dec == pytest.approx(0.5)
        assert c.loop.deadband_arcsec == pytest.approx(0.025)
        assert c.loop.max_command_arcsec == pytest.approx(2.45)
        assert c.loop.pacing_interval_s == pytest.approx(5.0)

    def test_quality_defaults(self):
        c = Config()
        assert c.quality.out_of_family_window == 20
        assert c.quality.out_of_family_warmup_n == 10
        assert c.quality.out_of_family_sigma == pytest.approx(5.0)
        assert c.quality.auto_resume_in_family == 3
        assert c.quality.stale_frame_timeout_s == pytest.approx(30.0)
        assert c.quality.target_switch_arcsec_threshold == pytest.approx(20.0)

    def test_reduction_defaults(self):
        c = Config()
        assert c.reduction.K == 1
        assert c.reduction.stride == 1
        assert c.reduction.stamp_x_halfwidth_px == 25
        assert c.reduction.stamp_y_lo == 600
        assert c.reduction.stamp_y_hi == 1980
        assert c.reduction.xcor_search_radius_px == 4
        assert c.reduction.auto_refresh_template is False

    def test_detector_defaults(self):
        c = Config()
        assert c.detector.gain_e_per_dn == pytest.approx(4.0)
        assert c.detector.read_noise_e == pytest.approx(12.0)
        assert c.detector.saturation_dn == 40000
        assert c.detector.y_middle_row == 1024


@pytest.mark.unit
class TestConfigRoundTrip:
    def test_save_then_load_returns_equal_config(self, tmp_path: Path):
        c = Config()
        c.loop.Kp_ra = 0.42  # mutate one value
        c.tcs.bind_host = "tcs.lco.test"  # ... and another
        out = tmp_path / "config.toml"
        save_config(c, out)
        c2 = load_config(out)
        assert c2 == c

    def test_load_missing_file_returns_defaults(self, tmp_path: Path):
        c = load_config(tmp_path / "does-not-exist.toml")
        assert c == Config()  # defaults

    def test_load_partial_toml_fills_in_defaults(self, tmp_path: Path):
        # Only [loop] section in the file; everything else should
        # default.
        f = tmp_path / "partial.toml"
        f.write_text("[loop]\nKp_ra = 0.99\n")
        c = load_config(f)
        assert c.loop.Kp_ra == pytest.approx(0.99)
        assert c.loop.Kp_dec == pytest.approx(0.5)  # default
        assert c.quality.out_of_family_sigma == pytest.approx(5.0)
