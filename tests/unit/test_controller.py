import pytest

from henrietta_guider.core.controller import Controller, ControllerConfig


@pytest.mark.unit
class TestController:
    def _make(self, **overrides):
        cfg = ControllerConfig(
            **{
                "Kp": 0.5,
                "Ki": 0.0,
                "Kd": 0.0,
                "deadband_arcsec": 0.025,
                "max_command_arcsec": 2.45,
                **overrides,
            }
        )
        return Controller(cfg)

    def test_zero_error_zero_command(self):
        ctrl = self._make()
        assert ctrl.step(0.0) == 0.0

    def test_proportional(self):
        ctrl = self._make(Kp=0.5)
        assert ctrl.step(0.10) == pytest.approx(0.05)

    def test_deadband_suppresses_small_errors(self):
        ctrl = self._make(deadband_arcsec=0.05)
        assert ctrl.step(0.04) == 0.0
        assert ctrl.step(-0.04) == 0.0

    def test_deadband_passes_threshold(self):
        ctrl = self._make(Kp=1.0, deadband_arcsec=0.05)
        assert ctrl.step(0.06) == pytest.approx(0.06)

    def test_max_command_clips(self):
        ctrl = self._make(Kp=1.0, max_command_arcsec=2.45)
        assert ctrl.step(+5.0) == pytest.approx(+2.45)
        assert ctrl.step(-5.0) == pytest.approx(-2.45)

    def test_deadband_pass_then_clip(self):
        # Combined: error passes the deadband AND requires clipping.
        ctrl = self._make(Kp=10.0, deadband_arcsec=0.05, max_command_arcsec=0.5)
        assert ctrl.step(0.06) == pytest.approx(0.5)

    def test_integral_does_not_accumulate_when_Ki_is_zero(self):
        # With Ki=0 (the v1 default) the integrator must stay at 0
        # forever, so a config-time Ki bump (no code change) doesn't
        # suddenly inject a huge accumulated error.
        ctrl = self._make(Kp=0.5, Ki=0.0)
        for _ in range(1000):
            ctrl.step(0.10)
        assert ctrl._integral == 0.0

    def test_integral_accumulates_when_Ki_is_nonzero(self):
        ctrl = self._make(Kp=0.5, Ki=0.01)
        for _ in range(10):
            ctrl.step(0.10)
        # 10 steps of +0.10" each, all above deadband:
        assert ctrl._integral == pytest.approx(1.0)

    def test_on_alerted_freezes_integral(self):
        # PI scenario: integral must NOT advance while frozen, must
        # resume on on_resumed().
        ctrl = self._make(Kp=0.5, Ki=0.01)
        for _ in range(5):
            ctrl.step(0.10)
        before = ctrl._integral
        ctrl.on_alerted()
        for _ in range(5):
            ctrl.step(0.10)
        assert ctrl._integral == before  # frozen
        ctrl.on_resumed()
        ctrl.step(0.10)
        assert ctrl._integral == pytest.approx(before + 0.10)

    def test_v1_pure_p_unaffected_by_alerted(self):
        # With Ki=Kd=0 (v1) the controller is stateless wrt _integral,
        # so on_alerted() doesn't change step() output.
        ctrl = self._make()
        ctrl.on_alerted()
        assert ctrl.step(0.10) == pytest.approx(0.05)
