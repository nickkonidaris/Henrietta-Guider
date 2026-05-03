"""Worker thread: owns the live pipeline.

  Watcher  ->  sutr_queue / slope_queue
                 |
                 v
              Reducer  -> MeasurementRow  -> Store + GUI event queue
                 |
                 v
            Controllers (RA, Dec)
                 |
                 v
              AutoGuiderServer -> wire frames

Plus quality monitor, target-switch detector, stale-frame watchdog,
template manager. Single thread; the GUI consumes from
`measurement_events` via root.after().
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .autoguider_server import AutoGuiderServer, ConnectionState
from .config import Config
from .controller import Controller, ControllerConfig
from .geometry import detector_to_sky
from .quality import OutOfFamilyDetector
from .reducer import Reducer
from .stale import StaleFrameWatchdog
from .store import FrameRecord, Store
from .target_switch import TargetSwitchDetector
from .template import Template, build_template
from .types import GuidingState, MeasurementRow, Stamp
from .watcher import Watcher

log = logging.getLogger(__name__)


@dataclass
class WorkerEvent:
    """Pushed onto Worker.measurement_events for the GUI to consume.

    Carries the just-computed rows, the current state, and the
    command-side info (so the GUI can plot commands_sent and surface
    pacing/disconnect/deadband suppressions).
    """

    rows: list[MeasurementRow]
    state: GuidingState
    cmd_ra_arcsec: float | None = None
    cmd_dec_arcsec: float | None = None
    cmd_suppressed_by: str | None = None
    field_rotation_deg: float | None = None
    # Latest raw SUTR for the image side-window. Not persisted; large
    # (~16 MB at 2048² float32). The TUI hands it to the matplotlib
    # subprocess via image_window.push_image(...).
    frame_image: np.ndarray | None = None


class Worker:
    def __init__(
        self,
        cfg: Config,
        watcher: Watcher,
        reducer: Reducer,
        tcs: AutoGuiderServer,
        store: Store,
        science_stamp: Stamp,
        bpm_good: np.ndarray,
    ) -> None:
        self.cfg = cfg
        self.watcher = watcher
        self.reducer = reducer
        self.tcs = tcs
        self.store = store
        self.science_stamp = science_stamp
        self.bpm_good = bpm_good

        self.controllers = (
            Controller(
                ControllerConfig(
                    Kp=cfg.loop.Kp_ra,
                    Ki=cfg.loop.Ki_ra,
                    Kd=cfg.loop.Kd_ra,
                    deadband_arcsec=cfg.loop.deadband_arcsec,
                    max_command_arcsec=cfg.loop.max_command_arcsec,
                )
            ),
            Controller(
                ControllerConfig(
                    Kp=cfg.loop.Kp_dec,
                    Ki=cfg.loop.Ki_dec,
                    Kd=cfg.loop.Kd_dec,
                    deadband_arcsec=cfg.loop.deadband_arcsec,
                    max_command_arcsec=cfg.loop.max_command_arcsec,
                )
            ),
        )
        self.quality = OutOfFamilyDetector(
            window=cfg.quality.out_of_family_window,
            warmup=cfg.quality.out_of_family_warmup_n,
            sigma_threshold=cfg.quality.out_of_family_sigma,
            auto_resume_in_family=cfg.quality.auto_resume_in_family,
        )
        self.stale = StaleFrameWatchdog(timeout_s=cfg.quality.stale_frame_timeout_s)
        self.target_switch = TargetSwitchDetector(
            threshold_arcsec=cfg.quality.target_switch_arcsec_threshold,
        )
        self.measurement_events: queue.Queue[WorkerEvent] = queue.Queue()
        # Live stamp + per-stamp template state. Keyed by stamp_id:
        #   0 = science  (drives RA/Dec corrections)
        #   1 = comparison / reference (second drift monitor)
        #   2 = rotation (drives field-rotation derivation)
        # The science slot is seeded from the constructor's science_stamp;
        # the others are added later via set_stamp() from the TUI's
        # `:1 / :2 / :3` commands. Templates are rebuilt automatically
        # whenever a stamp changes (if a slope file is in hand).
        self._stamps: dict[int, Stamp] = {0: science_stamp}
        self._templates: dict[int, Template] = {}
        self._last_slope_path: str | None = None
        self._stamps_lock = threading.Lock()
        self._state = GuidingState.IDLE
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @classmethod
    @contextmanager
    def run(
        cls,
        cfg: Config,
        watch_dir: str | Path,
        science_stamp: Stamp,
        bpm_good: np.ndarray,
        tcs_socket,
        settle_s: float = 0.2,
    ):
        """Convenience constructor for tests + CLI: builds and starts
        all the pieces, yields the worker, then cleans up on exit.

        `tcs_socket` is a test-only or test-fixture-supplied pre-accepted
        socket. The full bind/listen/accept loop with re-listen on
        disconnect lives in worker.py proper (Chunk 6 / 6.4)."""
        watcher = Watcher(settle_s=settle_s)
        watcher.start_unmanaged(watch_dir)
        reducer = Reducer(
            K=cfg.reduction.K,
            stride=cfg.reduction.stride,
            gain_e_per_dn=cfg.detector.gain_e_per_dn,
            bpm_good=bpm_good,
            xcor_search=cfg.reduction.xcor_search_radius_px,
        )
        tcs = AutoGuiderServer.from_connected_socket(
            tcs_socket,
            pacing_interval_s=cfg.loop.pacing_interval_s,
        )
        with Store.open(cfg.files.sqlite_db) as store:
            w = cls(
                cfg=cfg,
                watcher=watcher,
                reducer=reducer,
                tcs=tcs,
                store=store,
                science_stamp=science_stamp,
                bpm_good=bpm_good,
            )
            w._start_loop()
            try:
                yield w
            finally:
                w.stop()
                watcher.stop()

    def _start_loop(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    # ---- public stamp API (called from the TUI thread) -----------------

    def set_stamp(self, stamp_id: int, stamp: Stamp | None) -> None:
        """Add, replace, or remove a stamp at the given id.

        ``stamp_id`` follows the codebase convention:
          0 = science, 1 = comparison/reference, 2 = rotation.
        Pass ``stamp=None`` to remove the slot entirely. Templates are
        rebuilt automatically when a slope file has already been
        observed; otherwise the rebuild waits for the next slope file.
        """
        with self._stamps_lock:
            if stamp is None:
                self._stamps.pop(stamp_id, None)
                self._templates.pop(stamp_id, None)
                return
            self._stamps[stamp_id] = stamp
            self._templates.pop(stamp_id, None)  # invalidate old template
            self._build_template_for_stamp_locked(stamp_id, stamp)

    def _build_template_for_stamp_locked(self, stamp_id: int, stamp: Stamp) -> None:
        """Caller must hold self._stamps_lock."""
        if self._last_slope_path is None:
            return
        try:
            self._templates[stamp_id] = build_template(self._last_slope_path, stamp, self.bpm_good)
        except Exception as exc:
            log.warning(
                "template build failed for stamp %d (%s): %s",
                stamp_id,
                self._last_slope_path,
                exc,
            )

    def _loop(self) -> None:
        while not self._stop.is_set():
            # Drain the slope queue first (cheaper, only updates the
            # template).
            try:
                while True:
                    frame, path = self.watcher.slope_queue.get_nowait()
                    self._maybe_refresh_template(frame, path)
            except queue.Empty:
                pass

            # Stale-frame check fires even when no SUTRs arrive (the
            # whole point is to alert when the Archon stops producing).
            if self.stale.is_stale(t_now=time.monotonic()):
                self._enter_stale_alert()

            # Then process up to one SUTR per loop iteration.
            try:
                frame, sutr, raw_read, path = self.watcher.sutr_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            # Snapshot the current stamp/template list under lock; the
            # TUI may be calling set_stamp() concurrently.
            with self._stamps_lock:
                stamps_and_templates = [
                    (self._stamps[sid], self._templates[sid], sid)
                    for sid in sorted(self._stamps)
                    if sid in self._templates
                ]
            if 0 not in {sid for _, _, sid in stamps_and_templates}:
                # Need at least the science template to measure anything.
                continue

            rows = self.reducer.reduce_sutr(frame, sutr, raw_read, stamps_and_templates)
            if not rows:
                continue

            self.stale.note_accepted(t_now=time.monotonic())

            sci = next((r for r in rows if r.stamp_id == 0), rows[0])

            # Quality check on the science stamp's metrics. Only run when
            # the reducer actually produced xcor/trace fields (i.e. the
            # framebuffer has warmed up on this frame).
            if sci.dx_px is not None:
                verdict = self.quality.update(
                    {
                        "trace_flux_adu": sci.trace_flux_adu,
                        "trace_fwhm_x_px": sci.trace_fwhm_x_px,
                        "sky_background_adu": sci.sky_background_adu,
                        "xcor_peak_value": sci.xcor_peak_value,
                        "dx_px": sci.dx_px,
                        "dy_px": sci.dy_px,
                    }
                )
                if verdict.alerted:
                    self._state = GuidingState.ALERTED
                    self.controllers[0].on_alerted()
                    self.controllers[1].on_alerted()
                elif verdict.guiding and self._state is GuidingState.ALERTED:
                    self._state = GuidingState.GUIDING
                    self.controllers[0].on_resumed()
                    self.controllers[1].on_resumed()
                elif self._state is GuidingState.REFERENCE_SET and verdict.guiding:
                    self._state = GuidingState.GUIDING

            # Step controllers / send command. Suppression reasons are
            # tracked distinctly per spec §7 schema.
            cmd_ra, cmd_dec, suppressed = self._step_controllers(sci)

            # Field rotation (derived). When both science (id=0) and
            # rotation (id=2) stamps produced an xcor measurement on
            # this SUTR, compute the per-frame field rotation from
            # their differential pixel motion. Spec §4 "Field rotation
            # (derived)". NULL if either stamp is missing or warming
            # up.
            field_rotation_deg = self._compute_field_rotation(rows)

            # Persist.
            frame_rec = FrameRecord(
                frame_number=frame,
                sutr_number=sutr,
                timestamp_utc=time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
                frame_path=str(path),
                ramp_complete=False,
                ha_hours=None,
                dec_deg=None,
                pa_deg=None,
                airmass=None,
                temperature_c=None,
                focus_position=None,
                cmd_ra_arcsec=cmd_ra,
                cmd_dec_arcsec=cmd_dec,
                cmd_suppressed_by=suppressed,
                err_ra_arcsec=None,
                err_dec_arcsec=None,
                field_rotation_deg=field_rotation_deg,
                guiding_state=self._state.name,
            )
            self.store.write_frame(frame_rec, rows)
            # Operator display image: prefer the K-window difference
            # (the framebuffer's guide image — trace stands out, sky
            # drops away). On the first SUTR of a frame the framebuffer
            # is still in warmup, so fall back to the raw read so the
            # operator at least sees the integration starting.
            display_source = (
                self.reducer.last_guide_image
                if self.reducer.last_guide_image is not None
                else raw_read
            )
            # Mask known-bad pixels with NaN for the operator's display.
            # matplotlib's colormap renders NaN with the "bad" color
            # (transparent), so dead/hot pixels stop drawing the eye.
            display_image = np.where(self.bpm_good, display_source, np.nan).astype(np.float32)
            self.measurement_events.put(
                WorkerEvent(
                    rows=rows,
                    state=self._state,
                    cmd_ra_arcsec=cmd_ra,
                    cmd_dec_arcsec=cmd_dec,
                    cmd_suppressed_by=suppressed,
                    field_rotation_deg=field_rotation_deg,
                    frame_image=display_image,
                )
            )

    def _step_controllers(
        self,
        sci: MeasurementRow,
    ) -> tuple[float | None, float | None, str | None]:
        if sci.dx_px is None or sci.dy_px is None:
            return None, None, None
        if self._state is GuidingState.ALERTED:
            return None, None, "alerted"
        if self.tcs.state is not ConnectionState.CONNECTED:
            return None, None, "tcs_disconnected"
        dra, ddec = detector_to_sky(
            sci.dx_px,
            sci.dy_px,
            self.cfg.tcs.plate_scale_arcsec_per_px,
            self.cfg.tcs.pa_convention_offset_deg,
            self.cfg.tcs.parity_x,
            self.cfg.tcs.parity_y,
        )
        cmd_ra = self.controllers[0].step(dra)
        cmd_dec = self.controllers[1].step(ddec)
        if cmd_ra == 0.0 and cmd_dec == 0.0:
            return 0.0, 0.0, "deadband"
        # Snapshot suppression counters before send so we can detect
        # which path suppressed (pacing vs disconnect).
        before_pacing = self.tcs.commands_suppressed_pacing
        before_disc = self.tcs.commands_suppressed_disconnected
        sent = self.tcs.send_guide(cmd_ra, cmd_dec)
        if sent:
            return cmd_ra, cmd_dec, None
        if self.tcs.commands_suppressed_pacing > before_pacing:
            return cmd_ra, cmd_dec, "pacing"
        if self.tcs.commands_suppressed_disconnected > before_disc:
            return cmd_ra, cmd_dec, "tcs_disconnected"
        return cmd_ra, cmd_dec, "unknown"

    def _compute_field_rotation(
        self,
        rows: list[MeasurementRow],
    ) -> float | None:
        """Per spec §4 'Field rotation (derived)'.

        Returns the small-angle field-rotation estimate in degrees from
        the differential pixel motion of the science stamp (id=0) and
        the rotation stamp (id=2). Returns None when either is absent
        or warming up.
        """
        import math

        sci = next((r for r in rows if r.stamp_id == 0), None)
        rot = next((r for r in rows if r.stamp_id == 2), None)
        if sci is None or rot is None:
            return None
        if sci.dx_px is None or sci.dy_px is None or rot.dx_px is None or rot.dy_px is None:
            return None
        sx = rot.stamp_x_center - sci.stamp_x_center
        sy = (rot.stamp_y_lo + rot.stamp_y_hi) / 2.0 - (sci.stamp_y_lo + sci.stamp_y_hi) / 2.0
        d = math.sqrt(sx * sx + sy * sy)
        if d == 0.0:
            return None
        phi = math.atan2(sy, sx)
        ddx = rot.dx_px - sci.dx_px
        ddy = rot.dy_px - sci.dy_px
        theta_rad = (ddy * math.cos(phi) - ddx * math.sin(phi)) / d
        return math.degrees(theta_rad)

    def _enter_stale_alert(self) -> None:
        """State transition for the stale-frame timeout.

        Per spec §4 stale-frame watchdog: drops to REFERENCE_PENDING,
        discards the in-memory template, resets out-of-family stats,
        and (in the GUI) plays the stale-frame audio alert. This method
        is idempotent — calling it again while already in
        REFERENCE_PENDING is a no-op.
        """
        if self._state is GuidingState.REFERENCE_PENDING:
            return
        log.error("Stale-frame timeout — guiding stopped, no frames received.")
        self._state = GuidingState.REFERENCE_PENDING
        with self._stamps_lock:
            self._templates.clear()
        self.quality = OutOfFamilyDetector(
            window=self.cfg.quality.out_of_family_window,
            warmup=self.cfg.quality.out_of_family_warmup_n,
            sigma_threshold=self.cfg.quality.out_of_family_sigma,
            auto_resume_in_family=self.cfg.quality.auto_resume_in_family,
        )

    def _maybe_refresh_template(self, frame_number: int, path: str) -> None:
        with self._stamps_lock:
            self._last_slope_path = path
            need_refresh = not self._templates or self.cfg.reduction.auto_refresh_template
            if need_refresh:
                for sid, stamp in list(self._stamps.items()):
                    self._build_template_for_stamp_locked(sid, stamp)
            have_science = 0 in self._templates
        if have_science and self._state is GuidingState.IDLE:
            self._state = GuidingState.REFERENCE_SET
