"""Textual operator TUI for the Henrietta autoguider.

Runs as the main asyncio loop. Imports core.* but core does NOT
import tui.* (verified at import time). Drains
worker.measurement_events every 200 ms via App.set_interval; never
touches worker state directly.

The matplotlib image side-window lives in tui/image_window.py and runs
on its own thread (Task 7.2); the TUI does not import matplotlib at
all (so a textual-only / SSH-only run path stays clean).

TODO(commissioning): The TUI itself does NOT spin up a Worker (no TCP
listener inside the textual asyncio loop). In production the operator
runs `henrietta-cli` on the bring-up host (which owns the worker +
SQLite db) and `henrietta-tui` is a separate read-only front-end. See
Task 7.8 caveats and the deferral note in the plan.
"""

from __future__ import annotations

import argparse
import enum
import logging
import queue

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header

from henrietta_guider.core.config import Config, load_config  # noqa: F401
from henrietta_guider.core.types import GuidingState
from henrietta_guider.core.worker import Worker
from henrietta_guider.tui.estimate_k_dialog import EstimateKDialog  # noqa: F401
from henrietta_guider.tui.image_window import ImageWindow
from henrietta_guider.tui.settings_dialog import SettingsDialog  # noqa: F401
from henrietta_guider.tui.widgets.alerts import AlertBanner
from henrietta_guider.tui.widgets.control_panel import ControlPanel
from henrietta_guider.tui.widgets.timeseries import TimeSeries

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

    def __init__(
        self,
        *,
        demo: bool = False,
        config_path: str | None = None,
    ) -> None:
        super().__init__()
        self.state = GuidingState.IDLE
        self.worker: Worker | None = None
        self.image_window: ImageWindow | None = None
        self._latest_rotation: float | None = None
        self._demo = demo
        self._config_path = config_path
        self._control_panel = ControlPanel()
        self._alerts = AlertBanner(
            audio_alerts=False,
            audio_speak=False,
            audio_sound_path=None,
        )
        self._timeseries: list[TimeSeries] = [
            TimeSeries("dx (px)", lambda r: r.dx_px),
            TimeSeries("dy (px)", lambda r: r.dy_px),
            TimeSeries("fwhm (px)", lambda r: r.trace_fwhm_x_px),
            TimeSeries("flux (ADU)", lambda r: r.trace_flux_adu),
            TimeSeries("sky bg (ADU)", lambda r: r.sky_background_adu),
            TimeSeries("xcor peak", lambda r: r.xcor_peak_value),
            TimeSeries("snr √e⁻", lambda r: r.signal_snr),
            TimeSeries("rotation (deg)", lambda r: self._latest_rotation),
        ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield self._alerts
        with Horizontal():
            yield self._control_panel
            with Vertical():
                yield from self._timeseries
        yield Footer()

    def on_mount(self) -> None:
        self.image_window = ImageWindow()
        self.image_window.start()  # no-op on no-display systems
        self.set_interval(self.DRAIN_INTERVAL_S, self._drain_queue)
        if self._demo:
            self._start_demo_feed()

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
        """Hook: update widgets in response to a worker event."""
        sci = next((r for r in evt.rows if r.stamp_id == 0), None)
        if sci is None:
            return
        # Stash the per-event derived rotation so the rotation TimeSeries'
        # closure can read it (MeasurementRow is frozen, so we can't
        # decorate sci directly).
        self._latest_rotation = getattr(evt, "field_rotation_deg", None)
        for ts in self._timeseries:
            ts.append(sci)
        self._control_panel.update_readouts(
            sci.dx_px,
            sci.dy_px,
            sci.trace_fwhm_x_px,
            sci.xcor_peak_value,
            rotation_deg=self._latest_rotation,
        )
        if evt.state is not self.state:
            prev = self.state
            self.state = evt.state
            self._control_panel.update_buttons_for_state(evt.state)
            if evt.state is GuidingState.ALERTED:
                self._alerts.show(
                    "alert",
                    "Out of family — guiding paused.",
                )
            elif prev is GuidingState.ALERTED:
                self._alerts.hide()
        # TODO(7.8 / commissioning): push the latest guide image to
        # self.image_window.push_image(...). WorkerEvent does not carry
        # the raw guide image yet — wire that up alongside the
        # RectangleSelector landing in image_window.py.

    # --- demo feed (offline visual smoke) ------------------------------

    def _start_demo_feed(self) -> None:
        """When --demo is set, push synthetic WorkerEvents into
        _on_measurement. Lets the operator see the layout populate
        without a live worker."""
        import math
        import random
        import time

        from henrietta_guider.core.types import MeasurementRow
        from henrietta_guider.core.worker import WorkerEvent

        self._demo_t0 = time.time()

        def tick():
            t = time.time() - self._demo_t0
            sci = MeasurementRow(
                frame_number=int(t),
                sutr_number=1,
                stamp_id=0,
                signal_snr=10.0 + 2.0 * math.sin(t * 0.3),
                dx_px=0.5 * math.sin(t * 0.5),
                dy_px=0.4 * math.cos(t * 0.4),
                xcor_peak_value=1e6 + 1e5 * random.random(),
                xcor_curvature_x=1e3,
                xcor_curvature_y=1e3,
                trace_fwhm_x_px=2.5 + 0.3 * random.random(),
                trace_flux_adu=5e4 + 1e3 * random.random(),
                sky_background_adu=200.0 + 5.0 * random.random(),
                stamp_x_center=1024,
                stamp_x_halfwidth=25,
                stamp_y_lo=600,
                stamp_y_hi=1980,
                template_frame_number=0,
                quality_flags=(),
            )
            evt = WorkerEvent(
                rows=[sci],
                state=self.state,
                field_rotation_deg=0.001 * math.sin(t * 0.2),
            )
            self._on_measurement(evt)

        self.set_interval(1.0, tick)

    # --- action stubs --------------------------------------------------
    #
    # These fire the state machine and refresh the control-panel button
    # row. Where the worker would also need to be told (start / stop /
    # pause), the stub leaves a TODO for commissioning — see the
    # module-level docstring on why the TUI does not own the worker in
    # 7.8.

    def action_start(self) -> None:
        self.state = next_state(self.state, UiAction.START)
        self._control_panel.update_buttons_for_state(self.state)
        # TODO(commissioning): notify worker.

    def action_stop(self) -> None:
        self.state = next_state(self.state, UiAction.STOP)
        self._control_panel.update_buttons_for_state(self.state)
        # TODO(commissioning): notify worker.

    def action_pause(self) -> None:
        self.state = next_state(self.state, UiAction.PAUSE)
        self._control_panel.update_buttons_for_state(self.state)
        # TODO(commissioning): notify worker.

    def action_build_template(self) -> None:
        self.state = next_state(self.state, UiAction.TEMPLATE_BUILT)
        self._control_panel.update_buttons_for_state(self.state)
        # TODO(commissioning): notify worker to (re)build template.

    def action_estimate_k(self) -> None:
        # TODO(commissioning): push EstimateKDialog when the worker has
        # an active template + gain/read-noise pair to feed it.
        pass

    def action_settings(self) -> None:
        # TODO(commissioning): push SettingsDialog with the current
        # config + a save_path; reload on save.
        pass

    def action_toggle_image(self) -> None:
        # TODO(commissioning): show / hide the matplotlib side-window.
        pass

    def action_help(self) -> None:
        # TODO: render a help screen with the binding table.
        pass

    def action_change_watch_dir(self) -> None:
        """Open the textual DirectoryTree picker; on selection, restart
        the watcher and drop the state machine to REFERENCE_PENDING.
        Wired up at commissioning."""
        # TODO(commissioning): textual DirectoryTree picker.
        ...


def main() -> int:
    parser = argparse.ArgumentParser(prog="henrietta-tui")
    parser.add_argument(
        "--config",
        default="~/.config/henrietta_guider/config.toml",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Drive the layout with synthetic events (offline smoke).",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    app = HenriettaApp(demo=args.demo, config_path=args.config)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
