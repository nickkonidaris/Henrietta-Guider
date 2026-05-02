"""plotext-backed time-series widget for the textual TUI."""

from __future__ import annotations

import collections
from collections.abc import Callable

import plotext as plt
from rich.text import Text
from textual.widget import Widget


class TimeSeries(Widget):
    DEFAULT_CSS = "TimeSeries { height: 4; }"

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
            return Text(f"{self.title:>20}  (no data)")
        plt.clf()
        plt.theme("clear")
        plt.plotsize(self.size.width - 22, self.size.height)
        ys = [(y if y is not None else float("nan")) for y in self.buffer]
        plt.plot(ys)
        body = plt.build()
        return Text.from_ansi(f"{self.title:>20}  {body}")
