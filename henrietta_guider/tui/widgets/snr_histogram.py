"""plotext-backed histogram of integrated SNR.

Shows the rolling distribution of the per-frame integrated signal SNR
(`MeasurementRow.signal_snr` = sqrt(total_e) over the science stamp).
One scalar per frame is appended to a buffer; the widget renders a
histogram of the buffer's contents.
"""

from __future__ import annotations

import collections
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
        buffer: int = 600,
        bins: int = 25,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.title = title
        self.getter = getter
        self.bins = bins
        self.buffer: collections.deque[float] = collections.deque(maxlen=buffer)

    def append(self, row) -> None:
        v = self.getter(row)
        if v is not None:
            self.buffer.append(float(v))
        self.refresh()

    def render(self) -> Text:
        if not self.buffer:
            return Text(f"{self.title}: (no data)")
        plt.clf()
        plt.theme("clear")
        plt.frame(False)
        plt.title(self.title)
        plt.plotsize(self.size.width, self.size.height)
        plt.hist(list(self.buffer), bins=self.bins)
        return Text.from_ansi(plt.build())
