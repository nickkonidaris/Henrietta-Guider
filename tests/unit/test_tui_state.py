import pytest

from henrietta_guider.core.types import GuidingState
from henrietta_guider.tui.app import UiAction, next_state


@pytest.mark.unit
class TestStateTransitions:
    def test_idle_template_built_advances_to_set(self):
        assert next_state(GuidingState.IDLE, UiAction.TEMPLATE_BUILT) is GuidingState.REFERENCE_SET

    def test_set_start_guiding(self):
        assert next_state(GuidingState.REFERENCE_SET, UiAction.START) is GuidingState.GUIDING

    def test_guiding_stop_returns_to_set(self):
        assert next_state(GuidingState.GUIDING, UiAction.STOP) is GuidingState.REFERENCE_SET

    def test_guiding_pause(self):
        assert next_state(GuidingState.GUIDING, UiAction.PAUSE) is GuidingState.PAUSED

    def test_paused_resume(self):
        assert next_state(GuidingState.PAUSED, UiAction.RESUME) is GuidingState.GUIDING

    def test_stale_drops_to_reference_pending(self):
        for s in (GuidingState.GUIDING, GuidingState.ALERTED, GuidingState.PAUSED):
            assert next_state(s, UiAction.STALE) is GuidingState.REFERENCE_PENDING
