"""Textual operator TUI for the Henrietta autoguider.

Runs as the main asyncio loop. Imports core.* but core does NOT
import tui.* (verified at import time). Drains
worker.measurement_events every 200 ms via App.set_interval; never
touches worker state directly.

The matplotlib image side-window lives in tui/image_window.py and runs
on its own thread (Task 7.2); the TUI does not import matplotlib at
all (so a textual-only / SSH-only run path stays clean).
"""

from __future__ import annotations

import enum
import logging
import queue

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Static

from henrietta_guider.core.types import GuidingState
from henrietta_guider.core.worker import Worker

log = logging.getLogger(__name__)


class UiAction(enum.Enum):
    TEMPLATE_BUILT = "template_built"
    START = "start"
    STOP = "stop"
    PAUSE = "pause"
    RESUME = "resume"
    STALE = "stale"
    ALERT = "alert"
    RESUME_FAMILY = "resume_family"
    WATCH_DIR = "watch_dir"


_TRANSITIONS: dict[tuple[GuidingState, UiAction], GuidingState] = {
    (GuidingState.IDLE, UiAction.TEMPLATE_BUILT): GuidingState.REFERENCE_SET,
    (GuidingState.REFERENCE_PENDING, UiAction.TEMPLATE_BUILT): GuidingState.REFERENCE_SET,
    (GuidingState.REFERENCE_SET, UiAction.START): GuidingState.GUIDING,
    (GuidingState.GUIDING, UiAction.STOP): GuidingState.REFERENCE_SET,
    (GuidingState.GUIDING, UiAction.PAUSE): GuidingState.PAUSED,
    (GuidingState.PAUSED, UiAction.RESUME): GuidingState.GUIDING,
    (GuidingState.GUIDING, UiAction.ALERT): GuidingState.ALERTED,
    (GuidingState.ALERTED, UiAction.RESUME_FAMILY): GuidingState.GUIDING,
}


def next_state(current: GuidingState, action: UiAction) -> GuidingState:
    """Pure state-machine transition. Returns current if no transition."""
    if action is UiAction.STALE:
        if current is not GuidingState.IDLE:
            return GuidingState.REFERENCE_PENDING
        return current
    if action is UiAction.WATCH_DIR:
        return (
            GuidingState.REFERENCE_PENDING
            if current is not GuidingState.IDLE
            else GuidingState.IDLE
        )
    return _TRANSITIONS.get((current, action), current)


class HenriettaApp(App):
    """Textual operator app. Owns the state machine, queue-drain pump,
    and references to the worker + the (optional) image side-window."""

    CSS_PATH = None
    BINDINGS = [
        Binding("d", "draw_science", "Draw science"),
        Binding("a", "add_comparison", "Add comparison"),
        Binding("r", "add_rotation", "Add rotation"),
        Binding("b", "build_template", "Build template"),
        Binding("s", "start", "Start guiding"),
        Binding("t", "stop", "Stop guiding"),
        Binding("p", "pause", "Pause"),
        Binding("k", "estimate_k", "Estimate K"),
        Binding("comma", "settings", "Settings"),
        Binding("c", "change_watch_dir", "Change watch dir"),
        Binding("i", "toggle_image", "Show/hide image window"),
        Binding("question_mark", "help", "Help"),
    ]
    DRAIN_INTERVAL_S = 0.2

    def __init__(self) -> None:
        super().__init__()
        self.state = GuidingState.IDLE
        self.worker: Worker | None = None
        self.image_window = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Henrietta autoguider TUI — skeleton")
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(self.DRAIN_INTERVAL_S, self._drain_queue)

    def _drain_queue(self) -> None:
        if self.worker is None:
            return
        try:
            while True:
                evt = self.worker.measurement_events.get_nowait()
                self._on_measurement(evt)
        except queue.Empty:
            pass

    def _on_measurement(self, evt) -> None:
        """Hook: update widgets in response to a worker event.
        Filled in by Task 7.8 wire-up.
        """

    def action_change_watch_dir(self) -> None:
        """Open the textual DirectoryTree picker; on selection, restart
        the watcher and drop the state machine to REFERENCE_PENDING.
        Wired up in Task 7.8."""
        ...


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    app = HenriettaApp()
    app.run()
    return 0
