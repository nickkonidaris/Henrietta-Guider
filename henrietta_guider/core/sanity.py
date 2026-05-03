"""Sequential-order sanity checks for incoming SUTR / slope-frame events.

See spec §4 "Sequential-order sanity checks". Returns a SanityVerdict;
the worker acts on it.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class SanityAction(enum.Enum):
    ACCEPT = "accept"
    WARN_ACCEPT = "warn_accept"  # log WARN, accept the file
    WARN_DISCARD = "warn_discard"  # log WARN + audible alert, discard


@dataclass(frozen=True)
class SanityVerdict:
    action: SanityAction
    audible: bool  # True if the GUI should play the warning sound
    tags: tuple[str, ...]  # for log message + quality_flags


@dataclass
class SanityChecker:
    last_frame: int | None = None
    last_sutr: int | None = None

    def check(self, frame_number: int, sutr_number: int) -> SanityVerdict:
        # Across frames first, since a new frame resets per-frame state.
        if self.last_frame is not None and frame_number < self.last_frame:
            return SanityVerdict(SanityAction.WARN_DISCARD, True, ("frame_backwards",))

        tags: list[str] = []
        if self.last_frame is not None and frame_number > self.last_frame + 1:
            # Skipped frame numbers — normal operationally (operator
            # aborted exposures); log at INFO via a tag the caller maps.
            tags.append("frame_skip")

        new_frame = self.last_frame is None or frame_number != self.last_frame
        if new_frame:
            # Reset per-frame SUTR tracker; sutr should be 1 normally
            # but we're tolerant of any value at frame boundary.
            self.last_frame = frame_number
            self.last_sutr = sutr_number
            return SanityVerdict(SanityAction.ACCEPT, False, tuple(tags))

        # Within the same frame: enforce monotonicity.
        assert self.last_sutr is not None
        if sutr_number <= self.last_sutr:
            return SanityVerdict(
                SanityAction.WARN_DISCARD,
                True,
                (*tags, "sutr_out_of_order"),
            )
        if sutr_number > self.last_sutr + 1:
            self.last_sutr = sutr_number
            return SanityVerdict(
                SanityAction.WARN_ACCEPT,
                False,
                (*tags, "sutr_skip"),
            )
        self.last_sutr = sutr_number
        return SanityVerdict(SanityAction.ACCEPT, False, tuple(tags))
