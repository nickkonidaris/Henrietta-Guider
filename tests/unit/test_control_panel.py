import pytest

from henrietta_guider.core.types import GuidingState
from henrietta_guider.tui.widgets.control_panel import buttons_for_state


@pytest.mark.unit
class TestButtonMatrix:
    def test_idle_all_disabled(self):
        m = buttons_for_state(GuidingState.IDLE)
        assert m == {"build": False, "start": False, "stop": False, "pause": False}

    def test_reference_pending_only_build(self):
        m = buttons_for_state(GuidingState.REFERENCE_PENDING)
        assert m == {"build": True, "start": False, "stop": False, "pause": False}

    def test_reference_set_build_and_start(self):
        m = buttons_for_state(GuidingState.REFERENCE_SET)
        assert m == {"build": True, "start": True, "stop": False, "pause": False}

    def test_guiding_stop_pause_active(self):
        m = buttons_for_state(GuidingState.GUIDING)
        assert m == {"build": True, "start": False, "stop": True, "pause": True}

    def test_alerted_same_as_guiding(self):
        # Alerted is "still in the loop, just shouting"; same buttons.
        assert buttons_for_state(GuidingState.ALERTED) == buttons_for_state(GuidingState.GUIDING)

    def test_paused_keeps_pause_active(self):
        # The button itself stays clickable (its label flips to Resume).
        m = buttons_for_state(GuidingState.PAUSED)
        assert m["pause"] is True
        assert m["stop"] is True
        assert m["start"] is False

    def test_all_states_covered(self):
        # Every GuidingState value has an entry.
        for s in GuidingState:
            buttons_for_state(s)  # must not KeyError
