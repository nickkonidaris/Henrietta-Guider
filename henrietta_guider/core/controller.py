"""Per-axis P controller (with PI/PID hooks for forward compatibility).

The controller takes a measured error in arcseconds and returns the
command in arcseconds. Dead band suppresses noise-floor commands; the
max-command clip keeps a single command within the wire range. v1 uses
pure-P; Ki and Kd live in the config and are used once the PI/PID
machinery is added.

Sign convention: step() is called with `error_arcsec = -measured_drift`
already converted to sky frame by geometry.detector_to_sky(). The
controller multiplies by Kp and returns the command directly (no
sign flip here).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ControllerConfig:
    Kp: float = 0.5
    Ki: float = 0.0
    Kd: float = 0.0
    deadband_arcsec: float = 0.025
    max_command_arcsec: float = 2.45


class Controller:
    def __init__(self, cfg: ControllerConfig) -> None:
        self.cfg = cfg
        # Reserved for PI/PID; unused in v1.
        self._integral: float = 0.0
        self._last_error: float | None = None
        self._frozen: bool = False

    def step(self, error_arcsec: float) -> float:
        """Compute the command for one error sample."""
        if abs(error_arcsec) < self.cfg.deadband_arcsec:
            return 0.0
        cmd = self.cfg.Kp * error_arcsec
        # Ki / Kd hooks. Disabled when frozen and skipped entirely when
        # Ki == 0 (v1) so the integral never grows. This prevents a
        # config-time Ki bump from suddenly injecting a huge accumulated
        # error from a long previous run.
        if not self._frozen and self.cfg.Ki != 0.0:
            self._integral += error_arcsec
        if not self._frozen:
            self._last_error = error_arcsec
        cmd += self.cfg.Ki * self._integral
        # (Kd term omitted in v1; would use _last_error here.)
        # Clip.
        if cmd > self.cfg.max_command_arcsec:
            cmd = self.cfg.max_command_arcsec
        elif cmd < -self.cfg.max_command_arcsec:
            cmd = -self.cfg.max_command_arcsec
        return cmd

    def on_alerted(self) -> None:
        """Freeze integral / derivative accumulators while ALERTED.

        v1: no-op (pure-P, stateless). When PI/PID is enabled later,
        this will stop _integral and _last_error from updating during
        ALERTED so the loop resumes cleanly without wind-up.
        """
        self._frozen = True

    def on_resumed(self) -> None:
        """Re-enable accumulators after ALERTED -> GUIDING."""
        self._frozen = False
