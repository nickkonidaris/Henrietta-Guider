"""Operator control panel: status / stamps / template / loop / tools.

A textual Widget composing Static labels and Button widgets. The App
(Task 7.8) calls update_readouts / update_template_label /
update_buttons_for_state on each measurement event or state change.

Buttons are wired by id; the App's `on_button_pressed` dispatches
based on `event.button.id` to the appropriate UiAction.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Button, Static

from henrietta_guider.core.types import GuidingState

# Single source of truth for state -> button enabled/disabled.
# True = enabled, False = disabled.
_BUTTON_MATRIX: dict[GuidingState, dict[str, bool]] = {
    GuidingState.IDLE: {"build": False, "start": False, "stop": False, "pause": False},
    GuidingState.REFERENCE_PENDING: {"build": True, "start": False, "stop": False, "pause": False},
    GuidingState.REFERENCE_SET: {"build": True, "start": True, "stop": False, "pause": False},
    GuidingState.GUIDING: {"build": True, "start": False, "stop": True, "pause": True},
    GuidingState.ALERTED: {"build": True, "start": False, "stop": True, "pause": True},
    GuidingState.PAUSED: {"build": True, "start": False, "stop": True, "pause": True},
}


def buttons_for_state(state: GuidingState) -> dict[str, bool]:
    """Pure lookup. Returns {build, start, stop, pause} -> enabled bool."""
    return _BUTTON_MATRIX[state]


class ControlPanel(Widget):
    """Left-side panel: status / stamps / template / loop / tools.

    Holds Button + Static children, exposes three update_* methods the
    app calls on each measurement event or state change.
    """

    DEFAULT_CSS = """
    ControlPanel { width: 40; padding: 1; }
    ControlPanel #status   { color: #93C5FD; }
    ControlPanel #readouts { color: #E5E7EB; }
    """

    def __init__(self) -> None:
        super().__init__()
        self._status = Static("status: idle", id="status")
        self._template = Static("template: none", id="template")
        self._readouts = Static("dx --  dy --  fwhm --  xcor --", id="readouts")
        self._btn_build = Button("Build Template", id="build")
        self._btn_start = Button("Start", id="start")
        self._btn_stop = Button("Stop", id="stop")
        self._btn_pause = Button("Pause", id="pause")

    def compose(self) -> ComposeResult:
        yield Vertical(
            self._status,
            Static("─── Stamps ───"),
            Static("Draw science / Add comparison / Add rotation"),
            Static("─── Template ───"),
            self._template,
            self._btn_build,
            Static("─── Loop ───"),
            self._btn_start,
            self._btn_stop,
            self._btn_pause,
            Static("─── Live ───"),
            self._readouts,
            Static("─── Tools ───"),
            Static("[k] Estimate K   [,] Settings"),
        )

    # --- update hooks called by the App on measurement events ----------

    def update_readouts(
        self,
        dx: float | None,
        dy: float | None,
        fwhm: float | None,
        xcor_peak: float | None,
        rotation_deg: float | None = None,
    ) -> None:
        def f(x: float | None) -> str:
            return f"{x:+.3f}" if x is not None else "  --"

        line = f"dx {f(dx)}  dy {f(dy)}  fwhm {f(fwhm)}  xcor {f(xcor_peak)}"
        if rotation_deg is not None:
            line += f"  rot {rotation_deg:+.4f}°"
        self._readouts.update(line)

    def update_template_label(self, frame_number: int | None) -> None:
        if frame_number is None:
            self._template.update("template: none")
        else:
            self._template.update(f"template: hen{frame_number:04d}")

    def update_buttons_for_state(self, state: GuidingState) -> None:
        m = buttons_for_state(state)
        self._btn_build.disabled = not m["build"]
        self._btn_start.disabled = not m["start"]
        self._btn_stop.disabled = not m["stop"]
        # Pause button label flips to Resume in PAUSED.
        self._btn_pause.disabled = not m["pause"]
        self._btn_pause.label = "Resume" if state is GuidingState.PAUSED else "Pause"
        # Status line.
        self._status.update(f"status: {state.name.lower()}")
