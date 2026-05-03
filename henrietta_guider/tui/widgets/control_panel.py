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
    ControlPanel #sky      { color: #E5E7EB; }
    ControlPanel #pa       { color: #93C5FD; }
    ControlPanel #cmd      { color: #FBBF24; }
    """

    def __init__(self) -> None:
        super().__init__()
        self._status = Static("status: idle", id="status")
        self._template = Static("template: none", id="template")
        self._readouts = Static("dx --  dy --  fwhm --  xcor --", id="readouts")
        self._sky_readouts = Static('dRA --"  dDec --"', id="sky")
        self._pa_readout = Static("PA: --", id="pa")
        self._cmd = Static("cmd: --", id="cmd")
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
            self._sky_readouts,
            self._pa_readout,
            self._cmd,
            Static("─── Tools ───"),
            Static("[k] Estimate K   [,] Settings"),
        )

    # --- update hooks called by the App on measurement events ----------

    def update_readouts(
        self,
        dx: float | None,
        dy: float | None,
        fwhm: float | None,
    ) -> None:
        # xcor peak and rotation are already on the plots; keep this
        # line tight so the panel doesn't wrap.
        def f2(x: float | None) -> str:
            return f"{x:+.2f}" if x is not None else "  --"

        self._readouts.update(
            f"dx {f2(dx)}px  dy {f2(dy)}px  fwhm {f2(fwhm)}px"
        )

    def update_sky(
        self,
        dra_arcsec: float | None,
        ddec_arcsec: float | None,
        pa_deg: float | None,
    ) -> None:
        def f2(x: float | None, suffix: str = "") -> str:
            return f"{x:+.2f}{suffix}" if x is not None else f"  --{suffix}"

        self._sky_readouts.update(f"dRA {f2(dra_arcsec, chr(34))}  dDec {f2(ddec_arcsec, chr(34))}")
        self._pa_readout.update(f"PA: {f2(pa_deg)}°" if pa_deg is not None else "PA: --")

    def update_command(
        self,
        cmd_ra: float | None,
        cmd_dec: float | None,
        suppressed_by: str | None,
    ) -> None:
        """Reflect the latest controller decision.

        ``suppressed_by`` is one of ``"deadband" | "pacing" | "alerted" |
        "tcs_disconnected" | None`` (None means a real send happened).
        ``cmd_ra``/``cmd_dec`` are None during framebuffer warmup OR when
        suppressed by a non-deadband reason.
        """
        if suppressed_by == "alerted":
            self._cmd.update("cmd: -- (alerted by quality monitor)")
            return
        if suppressed_by == "tcs_disconnected":
            self._cmd.update("cmd: -- (TCS disconnected)")
            return
        if cmd_ra is None or cmd_dec is None:
            self._cmd.update("cmd: -- (warming up)")
            return
        head = f'cmd: RA{cmd_ra:+.3f}" Dec{cmd_dec:+.3f}"'
        if suppressed_by:
            self._cmd.update(f"{head}  [suppressed: {suppressed_by}]")
        else:
            self._cmd.update(f"{head}  [sent]")

    def update_template_label(self, frame_number: int | None, k: int | None = None) -> None:
        suffix = f"  K={k}" if k is not None else ""
        if frame_number is None:
            self._template.update(f"template: none{suffix}")
        else:
            self._template.update(f"template: hen{frame_number:04d}{suffix}")

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
