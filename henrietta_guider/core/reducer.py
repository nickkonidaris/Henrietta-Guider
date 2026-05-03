"""Per-SUTR orchestrator.

Reducer.reduce_sutr() takes one new raw read plus a list of
(Stamp, Template, stamp_id) tuples and produces one MeasurementRow per
stamp. It owns:

  - SanityChecker  (rejects out-of-order SUTRs / backwards frames)
  - FrameBuffer    (rolling K-window diff buffer)
  - reset_read     (this frame's _001 read; for signal_snr)
  - gain_e_per_dn  (detector gain)
  - bpm_good       (full-detector good-pixel mask)

It does not do anything I/O-related; the worker thread reads the FITS,
calls reduce_sutr(), and persists the resulting rows.
"""

from __future__ import annotations

import logging

import numpy as np

from .framebuffer import FrameBuffer
from .sanity import SanityAction, SanityChecker
from .sky import subtract_local_sky
from .template import Template
from .types import MeasurementRow, Stamp
from .xcor import xcor_2d


class Reducer:
    def __init__(
        self,
        K: int,
        stride: int,
        gain_e_per_dn: float,
        bpm_good: np.ndarray,
        xcor_search: int = 4,
    ) -> None:
        self.framebuffer = FrameBuffer(K=K, stride=stride)
        self.sanity = SanityChecker()
        self.gain_e_per_dn = gain_e_per_dn
        self.bpm_good = bpm_good
        self.xcor_search = xcor_search
        self._reset_read: np.ndarray | None = None
        self._reset_read_frame: int | None = None
        self._warned: set[str] = set()
        # Latest K-window guide image emitted by the framebuffer, or
        # None during warmup. Exposed so the worker can publish it for
        # the operator's image side-window.
        self.last_guide_image: np.ndarray | None = None

    def reduce_sutr(
        self,
        frame_number: int,
        sutr_number: int,
        raw_read: np.ndarray,
        stamps_and_templates: list[tuple[Stamp, Template, int]],
    ) -> list[MeasurementRow]:
        verdict = self.sanity.check(frame_number, sutr_number)
        if verdict.action is SanityAction.WARN_DISCARD:
            return []

        # On a new frame, capture this read as the reset.
        if self._reset_read_frame != frame_number:
            self._reset_read = raw_read.copy()
            self._reset_read_frame = frame_number

        # K-window difference (None if buffer not warm yet).
        guide_image = self.framebuffer.add(frame_number, sutr_number, raw_read)
        self.last_guide_image = guide_image

        rows: list[MeasurementRow] = []
        for stamp, template, stamp_id in stamps_and_templates:
            rows.append(
                self._reduce_one_stamp(
                    frame_number,
                    sutr_number,
                    stamp_id,
                    raw_read,
                    guide_image,
                    stamp,
                    template,
                    verdict.tags,
                )
            )
        return rows

    # ---- internal --------------------------------------------------------

    def _reduce_one_stamp(
        self,
        frame: int,
        sutr: int,
        stamp_id: int,
        raw_read: np.ndarray,
        guide_image: np.ndarray | None,
        stamp: Stamp,
        template: Template,
        sanity_tags: tuple[str, ...],
    ) -> MeasurementRow:
        good_stamp = self.bpm_good[
            stamp.y_lo : stamp.y_hi,
            stamp.x_min : stamp.x_max,
        ]

        # signal_snr (always computed; relative to current frame's reset).
        snr = self._signal_snr(raw_read, stamp, good_stamp)

        # If no guide image yet, return early with xcor/trace fields None.
        if guide_image is None:
            return MeasurementRow(
                frame_number=frame,
                sutr_number=sutr,
                stamp_id=stamp_id,
                signal_snr=snr,
                dx_px=None,
                dy_px=None,
                xcor_peak_value=None,
                xcor_curvature_x=None,
                xcor_curvature_y=None,
                trace_fwhm_x_px=None,
                trace_flux_adu=None,
                sky_background_adu=None,
                stamp_x_center=stamp.x_center,
                stamp_x_halfwidth=stamp.x_halfwidth,
                stamp_y_lo=stamp.y_lo,
                stamp_y_hi=stamp.y_hi,
                template_frame_number=template.frame_number,
                quality_flags=sanity_tags,
            )

        # Sky-subtract the guide-image stamp.
        gi_stamp = guide_image[
            stamp.y_lo : stamp.y_hi,
            stamp.x_min : stamp.x_max,
        ]
        sub, per_row_sky = subtract_local_sky(gi_stamp, good_stamp)
        sub = np.where(good_stamp, sub, 0.0)

        # 2-D xcor against the template.
        xc = xcor_2d(sub, template.image, search=self.xcor_search)

        # Trace summary stats.
        flux = float(np.sum(np.where(good_stamp, sub, 0.0)))
        sky_bg = float(np.median(per_row_sky))
        fwhm = self._trace_fwhm(sub)

        return MeasurementRow(
            frame_number=frame,
            sutr_number=sutr,
            stamp_id=stamp_id,
            signal_snr=snr,
            dx_px=xc.dx_px,
            dy_px=xc.dy_px,
            xcor_peak_value=xc.peak_value,
            xcor_curvature_x=xc.curvature_x,
            xcor_curvature_y=xc.curvature_y,
            trace_fwhm_x_px=fwhm,
            trace_flux_adu=flux,
            sky_background_adu=sky_bg,
            stamp_x_center=stamp.x_center,
            stamp_x_halfwidth=stamp.x_halfwidth,
            stamp_y_lo=stamp.y_lo,
            stamp_y_hi=stamp.y_hi,
            template_frame_number=template.frame_number,
            quality_flags=sanity_tags,
        )

    def _signal_snr(
        self,
        raw_read: np.ndarray,
        stamp: Stamp,
        good_stamp: np.ndarray,
    ) -> float | None:
        """Per spec §4: NULL on (reset-read itself, zero unmasked pixels,
        or any path where total_e <= 0). NULL is signaled by returning
        None; the store maps None to SQL NULL. A WARNING is logged on
        the first occurrence per session per cause (not per frame) so
        the operator notices a misconfigured stamp without log spam.
        """
        if self._reset_read is None:
            return None
        if not good_stamp.any():
            self._warn_once("signal_snr: zero unmasked pixels in stamp")
            return None
        sig_DN = (
            raw_read[stamp.y_lo : stamp.y_hi, stamp.x_min : stamp.x_max]
            - self._reset_read[stamp.y_lo : stamp.y_hi, stamp.x_min : stamp.x_max]
        )
        sig_e = float(np.sum(np.where(good_stamp, sig_DN, 0.0))) * self.gain_e_per_dn
        if sig_e <= 0.0:
            self._warn_once("signal_snr: total_e <= 0 (sub-reset read)")
            return None
        return float(np.sqrt(sig_e))

    def _warn_once(self, message: str) -> None:
        if message in self._warned:
            return
        logging.getLogger(__name__).warning(message)
        self._warned.add(message)

    def _trace_fwhm(self, sub: np.ndarray) -> float:
        """Collapse along Y to a 1-D spatial profile; FWHM from second
        moment. v1 only: spec §4 specifies a 1-D Gaussian fit; promote
        this to scipy.optimize.curve_fit if the second-moment estimate
        is found insufficient during commissioning.
        """
        profile = sub.sum(axis=0)
        if profile.sum() <= 0:
            return float("nan")
        x = np.arange(profile.size)
        x_mean = float(np.sum(x * profile) / np.sum(profile))
        x_var = float(np.sum((x - x_mean) ** 2 * profile) / np.sum(profile))
        if x_var <= 0:
            return float("nan")
        sigma = np.sqrt(x_var)
        return float(2.355 * sigma)
