"""Out-of-family detector: running median + MAD with warmup and auto-resume.

Per spec §5: maintain a rolling window of in-family measurements per
metric. Once warmup completes, any new measurement deviating from the
metric's running median by > sigma_threshold * (1.4826 * MAD) flags
the frame as ALERTED. After auto_resume_in_family consecutive
in-family frames, state returns to GUIDING.

The detector is metric-agnostic: callers pass any dict of {name: value}.
Only the metrics present in the dict are checked.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class OutOfFamilyVerdict:
    alerted: bool  # True if THIS frame is an outlier
    warming_up: bool  # True until warmup is satisfied
    guiding: bool  # True == clean GUIDING state; False == ALERTED or pending-resume
    offenders: tuple[str, ...]  # which metrics tripped


@dataclass
class OutOfFamilyDetector:
    window: int = 20
    warmup: int = 10
    sigma_threshold: float = 5.0
    auto_resume_in_family: int = 3
    _buffers: dict[str, collections.deque[float]] = field(default_factory=dict)
    _in_family_warmup_count: int = 0
    _alerted: bool = False
    _consec_in_family_after_alert: int = 0

    MAD_SCALE: float = 1.4826  # sigma equivalent

    def update(self, metrics: dict[str, float]) -> OutOfFamilyVerdict:
        warming_up = self._in_family_warmup_count < self.warmup
        offenders: list[str] = []
        if not warming_up:
            for name, value in metrics.items():
                buf = self._buffers.get(name)
                if buf is None or len(buf) == 0:
                    continue
                med = float(np.median(buf))
                mad = float(np.median(np.abs(np.array(buf) - med)))
                sigma = self.MAD_SCALE * mad
                if sigma == 0.0:
                    # Degenerate (all buffered values identical): any
                    # deviation IS an outlier. Real data has scatter
                    # so this only matters for synthetic tests, but we
                    # define it cleanly here.
                    if value != med:
                        offenders.append(name)
                    continue
                if abs(value - med) > self.sigma_threshold * sigma:
                    offenders.append(name)

        is_in_family = not offenders

        # Update buffers with EVERY observed value. Rationale: if the
        # family shifts (e.g. flux baseline changes from 100 to 10000),
        # the rolling window must follow it -- otherwise alerts get
        # stuck forever against a stale median. The auto-resume state
        # machine handles transient outliers; the running median
        # handles regime shifts. During warmup `warming_up` is True so
        # the outlier check is skipped entirely, and warmup is "simply
        # being seeded" per spec §5.
        for name, value in metrics.items():
            buf = self._buffers.setdefault(name, collections.deque(maxlen=self.window))
            buf.append(value)
        if warming_up and is_in_family:
            self._in_family_warmup_count += 1

        # Alert / resume state machine.
        alerted_now = bool(offenders)
        if alerted_now:
            self._alerted = True
            self._consec_in_family_after_alert = 0
            guiding = False
        elif self._alerted:
            if is_in_family:
                self._consec_in_family_after_alert += 1
                if self._consec_in_family_after_alert >= self.auto_resume_in_family:
                    self._alerted = False
                    self._consec_in_family_after_alert = 0
                    guiding = True
                else:
                    guiding = False
            else:
                guiding = False
        else:
            guiding = (
                not warming_up
            )  # in clean GUIDING after warmup; PRE-warmup is "not alerted, not guiding-confirmed"

        return OutOfFamilyVerdict(
            alerted=alerted_now,
            warming_up=warming_up,
            guiding=guiding,
            offenders=tuple(offenders),
        )
