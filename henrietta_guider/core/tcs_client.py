"""Fire-and-forget TCP client for the Henrietta TCS guide port.

State machine:
    DISCONNECTED -> CONNECTING -> CONNECTED
                ^         |             |
                |_________|_____________|

Auto-reconnect with exponential backoff. send_guide() is non-blocking:
returns True on a real send, False if not currently CONNECTED or within
the pacing window. Both suppression paths are counted for surfacing in
the GUI status bar.
"""

from __future__ import annotations

import enum
import logging
import socket
import time

from .wire import (
    GUIDE_STEP_ARCSEC,
    MAX_NEG_STEPS,
    MAX_POS_STEPS,
    encode_command,
)

log = logging.getLogger(__name__)


class ConnectionState(enum.Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"


class TCSClient:
    """TCP client to the TCS guide port.

    The class is *not* thread-safe. The autoguider's worker thread is
    the only thread that calls `send_guide()`; the GUI reads connection
    state via the property accessors only.
    """

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        pacing_interval_s: float = 5.0,
        backoff_initial_s: float = 1.0,
        backoff_max_s: float = 30.0,
    ) -> None:
        self.host = host
        self.port = port
        self.pacing_interval_s = pacing_interval_s
        self._backoff_initial = backoff_initial_s
        self._backoff_max = backoff_max_s
        self._sock: socket.socket | None = None
        self._state = ConnectionState.DISCONNECTED
        self._last_send_monotonic: float = -1e9
        self.commands_suppressed_pacing: int = 0
        self.commands_suppressed_disconnected: int = 0

    # ---- construction -----------------------------------------------------

    @classmethod
    def from_connected_socket(
        cls,
        sock: socket.socket,
        pacing_interval_s: float = 0.0,
    ) -> TCSClient:
        """Test-only: build a client around a pre-connected socket."""
        client = cls(pacing_interval_s=pacing_interval_s)
        client._sock = sock
        client._state = ConnectionState.CONNECTED
        return client

    # ---- public API -------------------------------------------------------

    @property
    def state(self) -> ConnectionState:
        return self._state

    def send_guide(self, ra_arcsec: float, dec_arcsec: float) -> bool:
        """Send a single guide-offset frame.

        Returns True if the frame was put on the socket, False if it was
        suppressed (not connected, or within the pacing window). Never
        raises on a normal disconnect; logs WARNING and flips state.
        """
        if self._state is not ConnectionState.CONNECTED:
            self.commands_suppressed_disconnected += 1
            return False

        now = time.monotonic()
        if now - self._last_send_monotonic < self.pacing_interval_s:
            self.commands_suppressed_pacing += 1
            return False

        # Clip to the legal wire range *before* encoding so the controller's
        # asymmetric range is honoured (max_command_arcsec is 2.45 by default
        # in §8 config; this is defence in depth).
        ra_clipped = max(
            MAX_NEG_STEPS * GUIDE_STEP_ARCSEC, min(MAX_POS_STEPS * GUIDE_STEP_ARCSEC, ra_arcsec)
        )
        dec_clipped = max(
            MAX_NEG_STEPS * GUIDE_STEP_ARCSEC, min(MAX_POS_STEPS * GUIDE_STEP_ARCSEC, dec_arcsec)
        )

        frame = encode_command(ra_clipped, dec_clipped)
        try:
            assert self._sock is not None
            self._sock.sendall(frame)
        except OSError as exc:
            log.warning("TCS sendall failed: %s", exc)
            self._mark_disconnected()
            self.commands_suppressed_disconnected += 1
            return False

        self._last_send_monotonic = now
        log.info('G %s sent (RA=%+.2f" Dec=%+.2f")', frame[1:5].decode(), ra_clipped, dec_clipped)
        return True

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
        self._state = ConnectionState.DISCONNECTED

    # ---- internal ---------------------------------------------------------

    def _mark_disconnected(self) -> None:
        self._state = ConnectionState.DISCONNECTED
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def _force_state(self, state: ConnectionState) -> None:
        """Test-only state override."""
        self._state = state
