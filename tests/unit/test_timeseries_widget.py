import pytest

from henrietta_guider.tui.widgets.timeseries import TimeSeries


@pytest.mark.unit
class TestTimeSeriesBuffer:
    def test_buffer_stores_timestamped_values(self):
        # The widget can't run the textual reactive system off-mount, so
        # we exercise the buffer logic by pushing directly into the
        # deque (skipping .refresh()).
        w = TimeSeries(title="dx", getter=lambda r: r.x, window_s=100.0)

        for t, v in [(1.0, 1.0), (2.0, 2.0), (3.0, 3.0)]:
            w.buffer.append((t, v))
        assert list(w.buffer) == [(1.0, 1.0), (2.0, 2.0), (3.0, 3.0)]

    def test_getter_returns_none_pass_through(self):
        w = TimeSeries(title="dx", getter=lambda r: getattr(r, "x", None), window_s=100.0)

        class Row:
            pass

        # Direct deque poke skips the time-window trim.
        w.buffer.append((0.0, w.getter(Row())))
        assert list(w.buffer) == [(0.0, None)]
