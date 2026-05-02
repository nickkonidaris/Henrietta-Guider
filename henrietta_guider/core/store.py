"""SQLite store for the per-frame measurement archive.

Two tables (spec §7):
  - frames               one row per (frame_number, sutr_number)
  - stamp_measurements   one row per (frame_number, sutr_number, stamp_id)

WAL mode so reads (analysis tools) don't block writes. One commit per
write_frame() call; no explicit transaction across frames.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .types import MeasurementRow

_SCHEMA = """
CREATE TABLE IF NOT EXISTS frames (
    frame_number       INTEGER NOT NULL,
    sutr_number        INTEGER NOT NULL,
    timestamp_utc      TEXT,
    frame_path         TEXT,
    ramp_complete      INTEGER,
    ha_hours           REAL,
    dec_deg            REAL,
    pa_deg             REAL,
    airmass            REAL,
    temperature_c      REAL,
    focus_position     REAL,
    cmd_ra_arcsec      REAL,
    cmd_dec_arcsec     REAL,
    cmd_suppressed_by  TEXT,
    err_ra_arcsec      REAL,
    err_dec_arcsec     REAL,
    field_rotation_deg REAL,
    guiding_state      TEXT,
    PRIMARY KEY (frame_number, sutr_number)
);

CREATE TABLE IF NOT EXISTS stamp_measurements (
    frame_number          INTEGER NOT NULL,
    sutr_number           INTEGER NOT NULL,
    stamp_id              INTEGER NOT NULL,
    stamp_x_center        INTEGER,
    stamp_x_halfwidth     INTEGER,
    stamp_y_lo            INTEGER,
    stamp_y_hi            INTEGER,
    template_frame_number INTEGER,
    dx_px                 REAL,
    dy_px                 REAL,
    xcor_peak_value       REAL,
    xcor_curvature_x      REAL,
    xcor_curvature_y      REAL,
    trace_fwhm_x_px       REAL,
    trace_flux_adu        REAL,
    sky_background_adu    REAL,
    signal_snr            REAL,
    quality_flags         TEXT,
    PRIMARY KEY (frame_number, sutr_number, stamp_id),
    FOREIGN KEY (frame_number, sutr_number)
        REFERENCES frames(frame_number, sutr_number)
);

CREATE INDEX IF NOT EXISTS idx_frames_time  ON frames(timestamp_utc);
CREATE INDEX IF NOT EXISTS idx_frames_hadec ON frames(ha_hours, dec_deg);
"""


@dataclass(frozen=True)
class FrameRecord:
    """One row's worth of frame-level metadata (the science box's command,
    error, and per-frame state). Mirrors the `frames` table."""

    frame_number: int
    sutr_number: int
    timestamp_utc: str
    frame_path: str
    ramp_complete: bool
    ha_hours: float | None
    dec_deg: float | None
    pa_deg: float | None
    airmass: float | None
    temperature_c: float | None
    focus_position: float | None
    cmd_ra_arcsec: float | None
    cmd_dec_arcsec: float | None
    cmd_suppressed_by: str | None
    err_ra_arcsec: float | None
    err_dec_arcsec: float | None
    field_rotation_deg: float | None
    guiding_state: str


class Store:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @classmethod
    @contextmanager
    def open(cls, path: str | Path):
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(p))
        try:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.executescript(_SCHEMA)
            conn.commit()
            yield cls(conn)
        finally:
            conn.close()

    def write_frame(self, frame: FrameRecord, rows: list[MeasurementRow]) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO frames
               (frame_number, sutr_number, timestamp_utc, frame_path,
                ramp_complete, ha_hours, dec_deg, pa_deg, airmass,
                temperature_c, focus_position,
                cmd_ra_arcsec, cmd_dec_arcsec, cmd_suppressed_by,
                err_ra_arcsec, err_dec_arcsec, field_rotation_deg,
                guiding_state)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                frame.frame_number,
                frame.sutr_number,
                frame.timestamp_utc,
                frame.frame_path,
                int(frame.ramp_complete),
                frame.ha_hours,
                frame.dec_deg,
                frame.pa_deg,
                frame.airmass,
                frame.temperature_c,
                frame.focus_position,
                frame.cmd_ra_arcsec,
                frame.cmd_dec_arcsec,
                frame.cmd_suppressed_by,
                frame.err_ra_arcsec,
                frame.err_dec_arcsec,
                frame.field_rotation_deg,
                frame.guiding_state,
            ),
        )
        for row in rows:
            self._conn.execute(
                """INSERT OR REPLACE INTO stamp_measurements
                   (frame_number, sutr_number, stamp_id,
                    stamp_x_center, stamp_x_halfwidth, stamp_y_lo, stamp_y_hi,
                    template_frame_number, dx_px, dy_px,
                    xcor_peak_value, xcor_curvature_x, xcor_curvature_y,
                    trace_fwhm_x_px, trace_flux_adu, sky_background_adu,
                    signal_snr, quality_flags)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    row.frame_number,
                    row.sutr_number,
                    row.stamp_id,
                    row.stamp_x_center,
                    row.stamp_x_halfwidth,
                    row.stamp_y_lo,
                    row.stamp_y_hi,
                    row.template_frame_number,
                    row.dx_px,
                    row.dy_px,
                    row.xcor_peak_value,
                    row.xcor_curvature_x,
                    row.xcor_curvature_y,
                    row.trace_fwhm_x_px,
                    row.trace_flux_adu,
                    row.sky_background_adu,
                    row.signal_snr,
                    json.dumps(list(row.quality_flags)),
                ),
            )
        self._conn.commit()
