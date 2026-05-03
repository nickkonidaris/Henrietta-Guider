"""plotext-backed histogram of integrated SNR.

Shows the rolling distribution of the per-frame integrated signal SNR
(`MeasurementRow.signal_snr` = sqrt(total_e) over the science stamp).
One scalar per frame is appended to a time-windowed buffer; samples
older than `window_s` are trimmed so the histogram stays responsive.
"""

from __future__ import annotations

import collections
import time
from collections.abc import Callable

import plotext as plt
from rich.text import Text
from textual.widget import Widget


class SnrHistogram(Widget):
    # Same sizing rules as TimeSeries so the stacked panels divide
    # available height evenly.
    DEFAULT_CSS = """
    SnrHistogram {
        height: 1fr;
        min-height: 7;
    }
    """

    def __init__(
        self,
        title: str,
        getter: Callable,  # row -> float | None
        window_s: float = 100.0,
        bins: int = 25,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.title = title
        self.getter = getter
        self.window_s = window_s
        self.bins = bins
        # (monotonic-time, value) entries; trimmed each append so the
        # histogram reflects only the last `window_s` seconds of data.
        self.buffer: collections.deque[tuple[float, float]] = collections.deque()

    def append(self, row) -> None:
        v = self.getter(row)
        now = time.monotonic()
        if v is not None:
            self.buffer.append((now, float(v)))
        cutoff = now - self.window_s
        while self.buffer and self.buffer[0][0] < cutoff:
            self.buffer.popleft()
        self.refresh()

    def render(self) -> Text:
        if not self.buffer:
            return Text(f"{self.title}: (no data)")
        plt.clf()
        plt.theme("clear")
        plt.frame(False)
        plt.title(self.title)
        plt.plotsize(self.size.width, self.size.height)
        plt.hist([v for _, v in self.buffer], bins=self.bins)
        return Text.from_ansi(plt.build())
