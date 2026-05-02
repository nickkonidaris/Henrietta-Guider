import pytest

from henrietta_guider.core.sanity import (
    SanityAction,
    SanityChecker,
)


@pytest.mark.unit
class TestSanityChecker:
    def test_first_ever_file_accepted(self):
        ck = SanityChecker()
        v = ck.check(frame_number=1, sutr_number=1)
        assert v.action is SanityAction.ACCEPT

    def test_normal_sequence_accepted(self):
        ck = SanityChecker()
        for sutr in range(1, 5):
            v = ck.check(frame_number=10, sutr_number=sutr)
            assert v.action is SanityAction.ACCEPT

    def test_skipped_sutr_within_frame_warns_but_accepts(self):
        ck = SanityChecker()
        ck.check(10, 1)
        ck.check(10, 2)
        ck.check(10, 3)
        v = ck.check(10, 5)  # skipped 4
        assert v.action is SanityAction.WARN_ACCEPT
        assert "sutr_skip" in v.tags
        assert v.audible is False

    def test_out_of_order_sutr_warns_and_discards(self):
        ck = SanityChecker()
        ck.check(10, 1)
        ck.check(10, 2)
        ck.check(10, 5)
        v = ck.check(10, 3)
        assert v.action is SanityAction.WARN_DISCARD
        assert "sutr_out_of_order" in v.tags
        assert v.audible is True

    def test_repeated_sutr_warns_and_discards(self):
        ck = SanityChecker()
        ck.check(10, 1)
        ck.check(10, 2)
        v = ck.check(10, 2)
        assert v.action is SanityAction.WARN_DISCARD
        assert "sutr_out_of_order" in v.tags
        assert v.audible is True

    def test_skipped_frame_numbers_logged_at_info_level(self):
        ck = SanityChecker()
        ck.check(10, 1)
        v = ck.check(15, 1)  # 5 frames skipped — normal operation
        assert v.action is SanityAction.ACCEPT
        assert "frame_skip" in v.tags
        assert v.audible is False

    def test_backwards_frame_warns_and_discards(self):
        ck = SanityChecker()
        ck.check(15, 1)
        v = ck.check(10, 1)
        assert v.action is SanityAction.WARN_DISCARD
        assert "frame_backwards" in v.tags
        assert v.audible is True
