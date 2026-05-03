"""Stale-frame watchdog timer. Time is injected by the caller for
testability; in production use time.monotonic().

Per spec §4 "Stale-frame watchdog": tick is gated on having ever
accepted a guide image, and resets on watch-dir change and on each
new frame_number boundary so the inevitable warm-up delay of ~2K reads
on a new target does not falsely trip the alert.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StaleFrameWatchdog:
    timeout_s: float = 30.0
    _ever_accepted: bool = False
    _last_tick: float | None = None

    def note_accepted(self, t_now: float) -> None:
        self._ever_accepted = True
        self._last_tick = t_now

    def note_frame_boundary(self, t_now: float) -> None:
        # Don't change _ever_accepted; just reset the timer.
        self._last_tick = t_now

    def note_watch_dir_changed(self, t_now: float) -> None:
        self._ever_accepted = False
        self._last_tick = t_now

    def is_stale(self, t_now: float) -> bool:
        if not self._ever_accepted:
            return False
        if self._last_tick is None:
            return False
        return (t_now - self._last_tick) > self.timeout_s
