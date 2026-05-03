"""Shared dataclasses and enums used throughout core/.

Frozen dataclasses where the value is immutable per-frame data; plain
dataclasses where the object owns long-lived mutable state (e.g.
running-stat buffers).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class GuidingState(enum.Enum):
    IDLE = "idle"
    REFERENCE_PENDING = "reference_pending"
    REFERENCE_SET = "reference_set"
    GUIDING = "guiding"
    ALERTED = "alerted"
    PAUSED = "paused"


@dataclass(frozen=True)
class Stamp:
    """Rectangular window on the science detector.

    Coordinates are 0-based detector pixels. x_min and x_max are
    half-open: pixels in [x_min, x_max). Same for y_lo / y_hi.
    """

    x_center: int
    x_halfwidth: int
    y_lo: int
    y_hi: int

    @property
    def x_min(self) -> int:
        return self.x_center - self.x_halfwidth

    @property
    def x_max(self) -> int:
        # ALGORITHM.md uses [x_center - halfw : x_center + halfw + 1] —
        # the +1 gives a 2*halfw+1-wide window inclusive of x_center+halfw.
        return self.x_center + self.x_halfwidth + 1

    @property
    def shape(self) -> tuple[int, int]:
        """Returns (ny, nx) where nx = 2*x_halfwidth + 1."""
        return (self.y_hi - self.y_lo, self.x_max - self.x_min)


@dataclass(frozen=True)
class MeasurementRow:
    """One row per (frame_number, sutr_number, stamp_id) in stamp_measurements.

    xcor_* and trace_* fields are None when the K-window framebuffer
    has not yet warmed up on the current frame (no guide image). The
    signal_snr is also None per spec §4 if the stamp has zero unmasked
    pixels or total_e <= 0 (sub-reset read).
    """

    frame_number: int
    sutr_number: int
    stamp_id: int  # 0 = science, 1+ = comparison

    # Per-SUTR (always populated when call succeeded; None on edge cases):
    signal_snr: float | None  # None if zero unmasked pixels or total_e <= 0

    # Populated only when the framebuffer emits a guide image:
    dx_px: float | None
    dy_px: float | None
    xcor_peak_value: float | None
    xcor_curvature_x: float | None
    xcor_curvature_y: float | None
    trace_fwhm_x_px: float | None
    trace_flux_adu: float | None
    sky_background_adu: float | None

    # Provenance:
    stamp_x_center: int
    stamp_x_halfwidth: int
    stamp_y_lo: int
    stamp_y_hi: int
    template_frame_number: int | None  # which henNNNN.fits built the template
    quality_flags: tuple[str, ...] = ()
