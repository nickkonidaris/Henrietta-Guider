import pytest

from henrietta_guider.core.config import Config
from henrietta_guider.tui.settings_dialog import collect_values


@pytest.mark.unit
class TestCollectValues:
    def test_int_coercion(self):
        cfg = Config()
        new = collect_values(cfg, {("reduction", "K"): "3"})
        assert new.reduction.K == 3

    def test_float_coercion(self):
        cfg = Config()
        new = collect_values(cfg, {("loop", "Kp_ra"): "0.75"})
        assert new.loop.Kp_ra == pytest.approx(0.75)

    def test_bool_coercion(self):
        cfg = Config()
        new = collect_values(cfg, {("display", "audio_alerts"): "false"})
        assert new.display.audio_alerts is False
        new = collect_values(cfg, {("display", "audio_alerts"): "1"})
        assert new.display.audio_alerts is True

    def test_string_coercion(self):
        cfg = Config()
        new = collect_values(cfg, {("files", "sqlite_db"): "/tmp/g.db"})
        assert new.files.sqlite_db == "/tmp/g.db"

    def test_invalid_value_raises(self):
        cfg = Config()
        with pytest.raises(ValueError):
            collect_values(cfg, {("reduction", "K"): "not-an-int"})

    def test_does_not_mutate_input(self):
        # collect_values must return a fresh Config without mutating the
        # input cfg. Otherwise the dialog's Cancel button doesn't roll
        # back changes the user typed before clicking Save then Cancel.
        cfg = Config()
        original_K = cfg.reduction.K
        collect_values(cfg, {("reduction", "K"): "9"})
        assert original_K == cfg.reduction.K
