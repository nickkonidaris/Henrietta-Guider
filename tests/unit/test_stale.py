import pytest

from henrietta_guider.core.stale import StaleFrameWatchdog


@pytest.mark.unit
class TestStaleFrameWatchdog:
    def test_not_stale_before_first_accept(self):
        wd = StaleFrameWatchdog(timeout_s=30.0)
        # No guide image accepted yet -> never stale, no matter how
        # long has passed.
        assert wd.is_stale(t_now=120.0) is False

    def test_becomes_stale_after_timeout(self):
        wd = StaleFrameWatchdog(timeout_s=30.0)
        wd.note_accepted(t_now=10.0)
        assert wd.is_stale(t_now=39.0) is False
        assert wd.is_stale(t_now=41.0) is True

    def test_accept_resets_timer(self):
        wd = StaleFrameWatchdog(timeout_s=30.0)
        wd.note_accepted(t_now=10.0)
        wd.note_accepted(t_now=35.0)
        assert wd.is_stale(t_now=60.0) is False
        assert wd.is_stale(t_now=66.0) is True  # 35 + 31

    def test_frame_boundary_resets(self):
        wd = StaleFrameWatchdog(timeout_s=30.0)
        wd.note_accepted(t_now=10.0)
        wd.note_frame_boundary(t_now=29.0)
        # Boundary doesn't itself count as an accept, but resets the
        # timer so we don't false-trip during the 2K warmup.
        assert wd.is_stale(t_now=58.0) is False
        assert wd.is_stale(t_now=60.0) is True

    def test_watch_dir_change_disarms(self):
        wd = StaleFrameWatchdog(timeout_s=30.0)
        wd.note_accepted(t_now=10.0)
        wd.note_watch_dir_changed(t_now=20.0)
        # After dir change: must wait for a new accept before being stale.
        assert wd.is_stale(t_now=120.0) is False
        wd.note_accepted(t_now=130.0)
        assert wd.is_stale(t_now=161.0) is True
