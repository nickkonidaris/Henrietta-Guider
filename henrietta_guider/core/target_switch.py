"""Target-switch detection. Two signals, two severities (spec §4).

Pointing jump (>= threshold arcsec on-sky) -> full alert + spoken phrase
+ caller transitions to REFERENCE_PENDING. OBJECT-only change ->
soft signal + caller does a small beep. Pointing-jump wins on ties.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class TargetSwitchVerdict:
    severity: str  # "none" | "object_only" | "pointing"
    audible: bool  # play warning sound (pointing only)
    spoken_phrase: str | None  # speech text (pointing only)
    distance_arcsec: float  # 0.0 if no previous frame
    object_changed: bool


@dataclass
class TargetSwitchDetector:
    threshold_arcsec: float = 20.0
    _last_ra_deg: float | None = None
    _last_dec_deg: float | None = None
    _last_object: str | None = None

    def reset(self) -> None:
        self._last_ra_deg = None
        self._last_dec_deg = None
        self._last_object = None

    def update(
        self,
        ra_deg: float,
        dec_deg: float,
        object_name: str,
    ) -> TargetSwitchVerdict:
        if self._last_ra_deg is None:
            self._last_ra_deg = ra_deg
            self._last_dec_deg = dec_deg
            self._last_object = object_name
            return TargetSwitchVerdict("none", False, None, 0.0, False)

        # Compute on-sky distance with cos(Dec) correction on RA.
        cos_dec = math.cos(math.radians((dec_deg + self._last_dec_deg) / 2.0))
        d_ra_arc = (ra_deg - self._last_ra_deg) * cos_dec * 3600.0
        d_dec_arc = (dec_deg - self._last_dec_deg) * 3600.0
        dist = math.hypot(d_ra_arc, d_dec_arc)

        object_changed = object_name != self._last_object

        # Update before returning (so a subsequent call sees the new state).
        self._last_ra_deg = ra_deg
        self._last_dec_deg = dec_deg
        self._last_object = object_name

        if dist > self.threshold_arcsec:
            return TargetSwitchVerdict(
                "pointing",
                True,
                "target change possible",
                dist,
                object_changed,
            )
        if object_changed:
            return TargetSwitchVerdict(
                "object_only",
                False,
                None,
                dist,
                True,
            )
        return TargetSwitchVerdict("none", False, None, dist, False)
