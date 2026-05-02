"""Estimate K modal dialog: runs core.monte_carlo and shows results.

Per spec §9. Runs the Monte Carlo on a thread so the textual event
loop stays responsive. Result table + RMS-vs-K plot mirror the mockup
at mockups/estimate_k_mockup.png.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING

import plotext as plt
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Static

if TYPE_CHECKING:
    from henrietta_guider.core.monte_carlo import EstimateKResult
    from henrietta_guider.core.template import Template


class EstimateKDialog(ModalScreen):
    """Modal screen for the Monte Carlo K simulator.

    Caller passes in the active Template, gain, read noise, and an
    `on_apply` callback receiving the recommended K. The dialog runs
    estimate_k() on a worker thread and renders the result table +
    RMS-vs-K plot.
    """

    DEFAULT_CSS = """
    EstimateKDialog { align: center middle; }
    EstimateKDialog Vertical { width: 80; height: 24; padding: 1;
                                background: #20242C; }
    EstimateKDialog #plot { height: 12; padding: 1; }
    EstimateKDialog #status { color: #FBBF24; }
    EstimateKDialog DataTable { height: 8; }
    """

    def __init__(
        self,
        template: Template,
        gain_e_per_dn: float,
        read_noise_e: float,
        on_apply: Callable[[int], None],
        n_realisations: int = 50,
    ) -> None:
        super().__init__()
        self.template = template
        self.gain_e_per_dn = gain_e_per_dn
        self.read_noise_e = read_noise_e
        self.n_realisations = n_realisations
        self._on_apply = on_apply
        self._table = DataTable()
        self._plot = Static("", id="plot")
        self._status = Static("Running Monte Carlo...", id="status")
        self._btn_apply = Button("Apply recommended K", id="apply", disabled=True)
        self._btn_close = Button("Close", id="close")
        self._recommended_k: int | None = None

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("Estimate K — Monte Carlo simulator"),
            self._status,
            self._table,
            self._plot,
            self._btn_apply,
            self._btn_close,
        )

    async def on_mount(self) -> None:
        self._table.add_columns("K", "RMS dx (px)", "RMS dy (px)", "total RMS")
        from henrietta_guider.core.monte_carlo import estimate_k

        result = await asyncio.to_thread(
            estimate_k,
            self.template,
            gain_e_per_dn=self.gain_e_per_dn,
            read_noise_e=self.read_noise_e,
            n_realisations=self.n_realisations,
        )
        self._populate_table(result)
        self._draw_plot(result)
        self._enable_apply(result.recommended_K)

    def _populate_table(self, result: EstimateKResult) -> None:
        for row in result.rows:
            total = (row.rms_dx_px**2 + row.rms_dy_px**2) ** 0.5
            self._table.add_row(
                str(row.K),
                f"{row.rms_dx_px:.4f}",
                f"{row.rms_dy_px:.4f}",
                f"{total:.4f}",
            )

    def _draw_plot(self, result: EstimateKResult) -> None:
        plt.clf()
        plt.theme("clear")
        plt.plotsize(70, 10)
        ks = [r.K for r in result.rows]
        totals = [(r.rms_dx_px**2 + r.rms_dy_px**2) ** 0.5 for r in result.rows]
        plt.plot(ks, totals, marker="dot")
        plt.title("Total RMS vs K")
        plt.xlabel("K")
        plt.ylabel("RMS (px)")
        body = plt.build()
        self._plot.update(Text.from_ansi(body))

    def _enable_apply(self, k: int) -> None:
        self._recommended_k = k
        self._status.update(f"Recommended K = {k}")
        self._btn_apply.disabled = False
        self._btn_apply.label = f"Apply K = {k}"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "apply" and self._recommended_k is not None:
            self._on_apply(self._recommended_k)
            self.dismiss()
        elif event.button.id == "close":
            self.dismiss()
