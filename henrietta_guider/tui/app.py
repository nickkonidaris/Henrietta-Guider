"""Textual operator TUI for the Henrietta autoguider.

Runs as the main asyncio loop. Imports core.* but core does NOT
import tui.* (verified at import time). Drains
worker.measurement_events every 200 ms via App.set_interval; never
touches worker state directly.

The matplotlib image side-window lives in tui/image_window.py and runs
on its own thread (Task 7.2); the TUI does not import matplotlib at
all (so a textual-only / SSH-only run path stays clean).

By default `henrietta-tui` also starts the autoguider runtime (TCP
listener + Worker) concurrently in the same process via
henrietta_guider.runtime.AutoguiderRuntime. Pass --no-server to run
the TUI as a view-only client (e.g. when another process owns the
server, or when SSH'ing in to peek at the layout). --demo implies
--no-server and drives the layout with synthetic events.
"""

from __future__ import annotations

import argparse
import enum
import logging
import queue
from typing import TYPE_CHECKING

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Footer, Header

from henrietta_guider.core.config import Config, load_config
from henrietta_guider.core.types import GuidingState
from henrietta_guider.core.worker import Worker
from henrietta_guider.tui.estimate_k_dialog import EstimateKDialog
from henrietta_guider.tui.image_window import ImageWindow
from henrietta_guider.tui.settings_dialog import SettingsDialog
from henrietta_guider.tui.widgets.alerts import AlertBanner
from henrietta_guider.tui.widgets.control_panel import ControlPanel
from henrietta_guider.tui.widgets.snr_histogram import SnrHistogram
from henrietta_guider.tui.widgets.timeseries import TimeSeries

if TYPE_CHECKING:
    from henrietta_guider.runtime import AutoguiderRuntime

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
    DEFAULT_CSS = """
    /* The body Horizontal sits between Header/Alerts and Footer; it must
       claim the remaining vertical space so the time-series Vertical and
       the ControlPanel actually appear side-by-side at full height.
       Without this the Horizontal collapses to children's natural height
       and the ControlPanel's right column ends up out of view. */
    Horizontal { height: 1fr; }
    Horizontal > Vertical { height: 1fr; width: 1fr; }
    """
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
        # Arrow-key focus navigation (in addition to textual's default
        # Tab / Shift+Tab). Falls through any widget that consumes arrows
        # internally (Input cursor, DataTable selection, etc.).
        Binding("up", "focus_previous", show=False),
        Binding("down", "focus_next", show=False),
        Binding("left", "focus_previous", show=False),
        Binding("right", "focus_next", show=False),
    ]
    DRAIN_INTERVAL_S = 0.2

    def __init__(
        self,
        *,
        demo: bool = False,
        config_path: str | None = None,
        cfg: Config | None = None,
        runtime: AutoguiderRuntime | None = None,
    ) -> None:
        super().__init__()
        self.state = GuidingState.IDLE
        self.worker: Worker | None = None
        self.image_window: ImageWindow | None = None
        self._latest_rotation: float | None = None
        self._demo = demo
        self._config_path = config_path
        self.cfg = cfg
        self._runtime = runtime
        self._server_status: str | None = None
        self._control_panel = ControlPanel()
        self._alerts = AlertBanner(
            audio_alerts=False,
            audio_speak=False,
            audio_sound_path=None,
        )
        # dx/dy fix to ±2.5 px so the operator sees absolute pixel
        # motion against the controller's deadband / max-command range,
        # not autoscaled noise.
        self._timeseries: list[TimeSeries] = [
            TimeSeries("dx (px)", lambda r: r.dx_px, ylim=(-2.5, 2.5)),
            TimeSeries("dy (px)", lambda r: r.dy_px, ylim=(-2.5, 2.5)),
            TimeSeries("fwhm (px)", lambda r: r.trace_fwhm_x_px),
            TimeSeries("flux (ADU)", lambda r: r.trace_flux_adu),
            TimeSeries("sky bg (ADU)", lambda r: r.sky_background_adu),
            TimeSeries("xcor peak", lambda r: r.xcor_peak_value),
            TimeSeries("rotation (deg)", lambda r: self._latest_rotation),
        ]
        # SNR is a histogram of the per-frame integrated SNR scalar
        # (sqrt(total_e) over the science stamp), accumulated over a
        # rolling window. Operator wants to see the distribution, not
        # the time-series.
        self._snr_histogram = SnrHistogram(
            title="integrated SNR √(e⁻)",
            getter=lambda r: r.signal_snr,
        )
        # Stack: time-series + histogram (same widget category for
        # layout — see compose()).
        self._stack: list[Widget] = [*self._timeseries, self._snr_histogram]

    def compose(self) -> ComposeResult:
        yield Header()
        yield self._alerts
        with Horizontal():
            yield self._control_panel
            with Vertical():
                yield from self._stack
        yield Footer()

    def on_mount(self) -> None:
        self.image_window = ImageWindow()
        self.image_window.start()  # spawns matplotlib subprocess; safe on no-display
        self.set_interval(self.DRAIN_INTERVAL_S, self._drain_queue)
        if self._demo:
            self._start_demo_feed()

    def on_unmount(self) -> None:
        if self.image_window is not None:
            self.image_window.stop()

    def _drain_queue(self) -> None:
        if self.worker is None and self._runtime is not None:
            self.worker = self._runtime.worker  # may still be None
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
        self._snr_histogram.append(sci)
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
        # Push the latest raw frame to the matplotlib side-window.
        # No-op when the subprocess isn't running (e.g. user closed it).
        img = getattr(evt, "frame_image", None)
        if img is not None and self.image_window is not None and self.image_window.available:
            self.image_window.push_image(img)

    # --- demo feed (offline visual smoke) ------------------------------

    def _start_demo_feed(self) -> None:
        """When --demo is set, push synthetic WorkerEvents into
        _on_measurement. Lets the operator see the layout populate
        without a live worker."""
        import math
        import random
        import time

        import numpy as np

        from henrietta_guider.core.types import MeasurementRow
        from henrietta_guider.core.worker import WorkerEvent

        self._demo_t0 = time.time()
        rng = np.random.default_rng(0)

        def synth_image(t: float) -> np.ndarray:
            """Synthetic guide frame: noisy sky + a drifting Gaussian
            column trace. Drift is slow enough to see motion at 1 Hz."""
            ny, nx = 256, 256
            cx = nx / 2 + 8.0 * math.sin(t * 0.2)
            x = np.arange(nx)[None, :].astype(np.float32)
            sky = rng.normal(loc=200.0, scale=8.0, size=(ny, nx)).astype(np.float32)
            trace = (
                1500.0
                * np.exp(-((x - cx) ** 2) / (2 * 1.6**2))
                * np.ones((ny, 1), dtype=np.float32)
            )
            return sky + trace

        def tick():
            t = time.time() - self._demo_t0
            # Integrated SNR ~ sqrt(total_e). For a bright trace over
            # ~2000 stamp pixels with ~5000 e/pix, total_e ~ 10^7 and
            # sqrt ~ 3000. Vary slowly to mimic transparency drift.
            integrated_snr = 3000.0 + 200.0 * math.sin(t * 0.05) + 80.0 * random.random()
            sci = MeasurementRow(
                frame_number=int(t),
                sutr_number=1,
                stamp_id=0,
                signal_snr=integrated_snr,
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
            # Also push a synthetic guide image to the matplotlib
            # subprocess so the operator can see motion in --demo mode.
            if self.image_window is not None and self.image_window.available:
                self.image_window.push_image(synth_image(t))

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

    def action_draw_science(self) -> None:
        self.notify(
            "Draw science: drag a rectangle on the image window — not yet "
            "wired. Needs bidirectional IPC with the matplotlib subprocess.",
            severity="warning",
        )

    def action_add_comparison(self) -> None:
        self.notify(
            "Add comparison: not yet wired (commissioning follow-up).",
            severity="warning",
        )

    def action_add_rotation(self) -> None:
        self.notify(
            "Add rotation: not yet wired (commissioning follow-up).",
            severity="warning",
        )

    def action_estimate_k(self) -> None:
        """Push the Estimate K modal. Uses the worker's active template
        when available; falls back to a synthetic Gaussian-column
        template in --demo mode so the dialog is exercisable without a
        live pipeline."""
        template = self._active_template_or_demo()
        if template is None:
            log.warning("Estimate K: no active template (waiting on a slope frame).")
            return
        cfg = self._ensure_cfg()

        def on_apply(k: int) -> None:
            log.info("Estimate K: recommended K=%d (operator applied).", k)
            # TODO(commissioning): push K into the running worker via
            # a Worker.update_K(K) once that helper exists.

        self.push_screen(
            EstimateKDialog(
                template=template,
                gain_e_per_dn=cfg.detector.gain_e_per_dn,
                read_noise_e=cfg.detector.read_noise_e,
                on_apply=on_apply,
                # 50 realisations × 5 K values is ~3 s on a desktop;
                # reduce in demo so it returns near-instantly.
                n_realisations=20 if self._demo else 50,
            )
        )

    def action_settings(self) -> None:
        """Push the tabbed Settings modal. Saving persists TOML; loop /
        quality / reduction edits do not hot-reload (v1 limitation —
        operator restarts the autoguider)."""
        cfg = self._ensure_cfg()
        from pathlib import Path

        save_path = Path(self._config_path or "~/.config/henrietta_guider/config.toml").expanduser()

        def on_saved(new_cfg: Config) -> None:
            self.cfg = new_cfg
            log.info("Settings: saved to %s", save_path)

        self.push_screen(SettingsDialog(cfg=cfg, save_path=save_path, on_saved=on_saved))

    def _ensure_cfg(self) -> Config:
        """Load config lazily if the App was constructed without one
        (older test paths). Cached on self.cfg."""
        if self.cfg is None:
            self.cfg = load_config(self._config_path or "~/.config/henrietta_guider/config.toml")
        return self.cfg

    def _active_template_or_demo(self):
        """Return the worker's active template, or — in --demo mode —
        a synthesized Gaussian-column template so Estimate K renders.
        Returns None when no template is available and we're not in
        demo mode (operator must wait for a slope frame)."""
        if self.worker is not None and self.worker._template is not None:
            return self.worker._template
        if not self._demo:
            return None
        # Build a small synthetic template once, cache it.
        cached = getattr(self, "_demo_template", None)
        if cached is not None:
            return cached
        import numpy as np

        from henrietta_guider.core.template import Template
        from henrietta_guider.core.types import Stamp

        ny, nx = 200, 51
        sigma = 1.5
        x = np.arange(nx)[None, :].astype(np.float32)
        img = (1000.0 * np.exp(-((x - nx // 2) ** 2) / (2 * sigma**2))) * np.ones(
            (ny, 1), dtype=np.float32
        )
        good = np.ones(img.shape, dtype=bool)
        cached = Template(
            image=img.astype(np.float32),
            good=good,
            frame_number=0,
            stamp=Stamp(x_center=nx // 2, x_halfwidth=nx // 2, y_lo=0, y_hi=ny),
        )
        self._demo_template = cached
        return cached

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
        "--watch-dir",
        help="Directory the server should watch (required unless --no-server)",
    )
    parser.add_argument(
        "--bpm",
        default=None,
        help="Override files.bad_pixel_mask",
    )
    parser.add_argument(
        "--no-server",
        action="store_true",
        help=(
            "Run TUI only — no listener, no worker. Use when another process is running the server."
        ),
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Drive the layout with synthetic events (implies --no-server).",
    )
    args = parser.parse_args()

    # --demo implies --no-server (the demo feed substitutes for the worker).
    if args.demo:
        args.no_server = True

    if not args.no_server and not args.watch_dir:
        parser.error("--watch-dir is required unless --no-server is set")

    logging.basicConfig(level=logging.INFO)
    cfg = load_config(args.config)
    runtime = None
    if not args.no_server:
        # Imports kept inside this branch so the view-only / --demo path
        # does not pull in numpy / astropy / runtime (per Task 7.8 layering).
        from pathlib import Path

        from henrietta_guider.core.bpm import load_bpm
        from henrietta_guider.core.types import Stamp
        from henrietta_guider.runtime import AutoguiderRuntime

        bpm_path = Path(args.bpm or cfg.files.bad_pixel_mask).expanduser()
        bpm_good = load_bpm(bpm_path)
        sci_stamp = Stamp(
            x_center=cfg.detector.y_middle_row,
            x_halfwidth=cfg.reduction.stamp_x_halfwidth_px,
            y_lo=cfg.reduction.stamp_y_lo,
            y_hi=cfg.reduction.stamp_y_hi,
        )
        runtime = AutoguiderRuntime(
            cfg=cfg,
            watch_dir=args.watch_dir,
            science_stamp=sci_stamp,
            bpm_good=bpm_good,
            on_status=lambda s: log.info("server: %s", s),
        )
        runtime.start()
    try:
        app = HenriettaApp(
            cfg=cfg,
            demo=args.demo,
            runtime=runtime,
            config_path=args.config,
        )
        app.run()
    finally:
        if runtime is not None:
            runtime.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
