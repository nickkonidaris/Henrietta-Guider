import pytest

from henrietta_guider.core.types import GuidingState, Stamp


@pytest.mark.unit
class TestStamp:
    def test_constructor_and_attributes(self):
        s = Stamp(x_center=512, x_halfwidth=25, y_lo=600, y_hi=1980)
        assert s.x_center == 512
        assert s.x_halfwidth == 25
        assert s.y_lo == 600
        assert s.y_hi == 1980

    def test_xmin_xmax_helpers(self):
        # ALGORITHM.md uses [x_center - halfw : x_center + halfw + 1]
        # -> width = 2*halfw + 1, inclusive of x_center+halfw.
        s = Stamp(x_center=100, x_halfwidth=10, y_lo=0, y_hi=100)
        assert s.x_min == 90
        assert s.x_max == 111  # half-open: [90, 111) -> 21 columns

    def test_shape(self):
        s = Stamp(x_center=100, x_halfwidth=10, y_lo=200, y_hi=300)
        assert s.shape == (100, 21)  # (ny, 2*halfw + 1)

    def test_frozen(self):
        s = Stamp(x_center=0, x_halfwidth=1, y_lo=0, y_hi=1)
        with pytest.raises(Exception):  # noqa: B017
            s.x_center = 99  # frozen dataclass


@pytest.mark.unit
class TestGuidingState:
    def test_canonical_states_exist(self):
        # Pin the names that the GUI / state machine refer to.
        assert GuidingState.IDLE.name == "IDLE"
        assert GuidingState.REFERENCE_PENDING.name == "REFERENCE_PENDING"
        assert GuidingState.REFERENCE_SET.name == "REFERENCE_SET"
        assert GuidingState.GUIDING.name == "GUIDING"
        assert GuidingState.ALERTED.name == "ALERTED"
        assert GuidingState.PAUSED.name == "PAUSED"
