"""plotext-backed time-series widget for the textual TUI."""

from __future__ import annotations

import collections
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
        buffer: int = 600,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.title = title
        self.getter = getter
        self.buffer: collections.deque[float | None] = collections.deque(maxlen=buffer)

    def append(self, row) -> None:
        self.buffer.append(self.getter(row))
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
        ys = [(y if y is not None else float("nan")) for y in self.buffer]
        plt.plot(ys)
        return Text.from_ansi(plt.build())
