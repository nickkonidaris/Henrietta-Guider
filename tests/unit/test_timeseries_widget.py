import pytest

from henrietta_guider.tui.widgets.timeseries import TimeSeries


@pytest.mark.unit
class TestTimeSeriesBuffer:
    def test_append_and_buffer_size(self):
        w = TimeSeries(title="dx", getter=lambda r: r.x, buffer=4)

        # Avoid running the textual reactive system — just test the
        # buffer logic via direct deque pokes.
        class Row:
            def __init__(self, x):
                self.x = x

        # Simulate appends without calling .refresh() (would need a
        # mounted textual app context); push directly to the deque to
        # verify maxlen behaviour.
        for v in [1.0, 2.0, 3.0, 4.0, 5.0]:
            w.buffer.append(w.getter(Row(v)))
        assert list(w.buffer) == [2.0, 3.0, 4.0, 5.0]

    def test_getter_returns_none_pass_through(self):
        w = TimeSeries(title="dx", getter=lambda r: getattr(r, "x", None), buffer=4)

        class Row:
            pass

        w.buffer.append(w.getter(Row()))
        assert list(w.buffer) == [None]
