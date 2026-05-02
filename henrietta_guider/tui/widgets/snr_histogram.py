"""plotext-backed per-pixel SNR histogram widget.

Renders a histogram of per-pixel signal SNR for the science stamp,
where SNR = sqrt((raw_read - reset_read) * gain). The widget owns the
latest sample; the App pushes a new sample once per WorkerEvent.
"""

from __future__ import annotations

import plotext as plt
from rich.text import Text
from textual.widget import Widget


class SnrHistogram(Widget):
    # Same sizing rules as TimeSeries so the 8 stacked panels divide
    # available height evenly.
    DEFAULT_CSS = """
    SnrHistogram {
        height: 1fr;
        min-height: 7;
    }
    """

    def __init__(
        self,
        title: str = "SNR √(e⁻)",
        bins: int = 25,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.title = title
        self.bins = bins
        self._values: list[float] = []

    def update_values(self, values) -> None:
        """Replace the current sample. Pass a sequence of per-pixel SNR
        values, or None / empty to clear the panel."""
        self._values = list(values) if values else []
        self.refresh()

    def render(self) -> Text:
        if not self._values:
            return Text(f"{self.title}: (no data)")
        plt.clf()
        plt.theme("clear")
        plt.frame(False)
        plt.title(self.title)
        plt.plotsize(self.size.width, self.size.height)
        plt.hist(self._values, bins=self.bins)
        return Text.from_ansi(plt.build())
