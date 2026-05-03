"""plotext-backed time-series widget for the textual TUI.

X axis is wall-clock seconds (relative to "now" = right edge); old
samples roll off after `window_s`. Default window is 100 s so the
trace doesn't keep marching as more samples accumulate.
"""

from __future__ import annotations

import collections
import time
from collections.abc import Callable

import plotext as plt
from rich.text import Text
from textual.widget import Widget


class TimeSeries(Widget):
    # 1fr lets the 8-widget stack divide the available vertical space
    # equally; min-height keeps a useful Y range when the terminal is
    # short. plotext draws 3 reserved rows (title/x-axis/x-ticks) so a
    # height below ~7 collapses the plot to 1 row of data.
    DEFAULT_CSS = """
    TimeSeries {
        height: 1fr;
        min-height: 7;
    }
    """

    def __init__(
        self,
        title: str,
        getter: Callable,  # row -> float | None
        window_s: float = 100.0,
        ylim: tuple[float, float] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.title = title
        self.getter = getter
        self.window_s = window_s
        self.ylim = ylim
        # Each entry is (monotonic-time, value-or-None). Trimmed each
        # append so we never hold more than ~window_s seconds of data.
        self.buffer: collections.deque[tuple[float, float | None]] = collections.deque()

    def append(self, row) -> None:
        v = self.getter(row)
        now = time.monotonic()
        self.buffer.append((now, v))
        cutoff = now - self.window_s
        while self.buffer and self.buffer[0][0] < cutoff:
            self.buffer.popleft()
        self.refresh()

    def render(self) -> Text:
        if not self.buffer:
            return Text(f"{self.title}: (no data)")
        plt.clf()
        plt.theme("clear")
        # frame(False) drops plotext's box border — the top edge ran one
        # column past the bottom edge with the default frame, which looked
        # like a misaligned "overbar" above the plot. Title is rendered by
        # plotext (centered) so the multi-line body is self-consistent.
        plt.frame(False)
        plt.title(self.title)
        plt.plotsize(self.size.width, self.size.height)
        if self.ylim is not None:
            plt.ylim(self.ylim[0], self.ylim[1])
        # X = seconds ago (negative on the left, 0 on the right edge).
        # Pinning the X range stops the trace from marching as samples
        # accumulate.
        now = time.monotonic()
        xs = [t - now for t, _ in self.buffer]
        ys = [(v if v is not None else float("nan")) for _, v in self.buffer]
        plt.plot(xs, ys)
        plt.xlim(-self.window_s, 0)
        return Text.from_ansi(plt.build())
