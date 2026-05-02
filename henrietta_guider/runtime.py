"""Process-wide runtime helper: TCP listener + Worker lifecycle.

Owns the bind+listen+accept lifecycle for the TCS connection and the
Worker that consumes from it. Designed so both `henrietta-cli` (headless)
and `henrietta-tui` (concurrent UI + server) can share one
implementation. `core/*` does not import this module; this module
imports `core.*` only.

Usage (CLI):
    rt = AutoguiderRuntime(cfg=..., watch_dir=..., science_stamp=...,
                           bpm_good=...)
    rt.start()                  # returns immediately; accept runs in bg
    try:
        signal.pause()          # block until SIGINT
    finally:
        rt.stop()

Usage (TUI):
    rt = AutoguiderRuntime(cfg=..., watch_dir=..., science_stamp=...,
                           bpm_good=...,
                           on_status=lambda s: app.call_from_thread(
                               app.set_server_status, s))
    rt.start()                  # textual App owns the asyncio loop;
                                # rt runs concurrently on its own threads
    # On every set_interval tick the App polls rt.worker; once non-None,
    # it begins draining rt.worker.measurement_events.

Lifecycle states (broadcast via on_status):
    "listening"     after bind+listen succeed
    "connected"     after accept returns
    "running"       after Worker.run starts the loop
    "stopped"       after stop() drains everything
    "error: <msg>"  on any unhandled exception during accept/run
"""

from __future__ import annotations

import contextlib
import logging
import socket
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from henrietta_guider.core.config import Config
from henrietta_guider.core.types import Stamp
from henrietta_guider.core.worker import Worker

log = logging.getLogger(__name__)


class AutoguiderRuntime:
    """Owns the TCS listener + Worker lifecycle.

    Two background threads:
      - the accept thread: binds, listens, accepts, then enters Worker.run
        and blocks on a stop event.
      - the Worker's own internal thread (created by Worker.run / _loop).
    """

    def __init__(
        self,
        *,
        cfg: Config,
        watch_dir: str | Path,
        science_stamp: Stamp,
        bpm_good: np.ndarray,
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        self._cfg = cfg
        self._watch_dir = watch_dir
        self._science_stamp = science_stamp
        self._bpm_good = bpm_good
        self._on_status = on_status or (lambda _s: None)
        self._listener: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None
        self._stop_evt = threading.Event()
        self._listening_evt = threading.Event()
        self._connected_evt = threading.Event()
        self._worker: Worker | None = None
        self._lock = threading.Lock()

    @property
    def worker(self) -> Worker | None:
        """Currently running Worker, or None until accept completes."""
        with self._lock:
            return self._worker

    @property
    def bound_port(self) -> int | None:
        """Locally bound port, or None if the listener is not open.

        Useful for tests that bind to an ephemeral port (listen_port=0)
        and need to discover the OS-assigned port.
        """
        with self._lock:
            if self._listener is None:
                return None
            try:
                return self._listener.getsockname()[1]
            except OSError:
                return None

    def wait_for_listening(self, timeout_s: float = 2.0) -> bool:
        """Block until bind+listen has succeeded (or stop()/error)."""
        return self._listening_evt.wait(timeout=timeout_s)

    def wait_for_connected(self, timeout_s: float = 2.0) -> bool:
        """Block until accept() has returned a peer."""
        return self._connected_evt.wait(timeout=timeout_s)

    def start(self) -> None:
        """Begin listening; accept runs in a daemon thread.

        Idempotent only in the sense that re-calling raises RuntimeError.
        """
        if self._accept_thread is not None:
            raise RuntimeError("AutoguiderRuntime already started")
        self._accept_thread = threading.Thread(
            target=self._accept_and_run,
            daemon=True,
            name="autoguider-accept",
        )
        self._accept_thread.start()

    def stop(self, join_timeout_s: float = 3.0) -> None:
        """Tear everything down. Safe to call multiple times."""
        self._stop_evt.set()
        with self._lock:
            listener = self._listener
        if listener is not None:
            with contextlib.suppress(OSError):
                listener.close()  # interrupts a blocked accept()
        if self._accept_thread is not None:
            self._accept_thread.join(timeout=join_timeout_s)
        self._on_status("stopped")

    # ---- internal --------------------------------------------------

    def _accept_and_run(self) -> None:
        try:
            self._do_listen_and_run()
        except Exception:
            log.exception("AutoguiderRuntime: unhandled error")
            self._on_status("error: see logs")

    def _do_listen_and_run(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        with self._lock:
            self._listener = listener
        try:
            listener.bind((self._cfg.tcs.bind_host, self._cfg.tcs.listen_port))
            listener.listen(1)
        except OSError as exc:
            self._on_status(
                f"error: bind {self._cfg.tcs.bind_host}:{self._cfg.tcs.listen_port}: {exc}"
            )
            return
        # Use the actual bound port so on_status reflects an OS-assigned
        # ephemeral port when the user passed listen_port=0.
        actual_port = listener.getsockname()[1]
        self._listening_evt.set()
        self._on_status(f"listening on {self._cfg.tcs.bind_host}:{actual_port}")
        try:
            sock, peer = listener.accept()
        except OSError as exc:
            # Closed by stop() while accept() was blocked, or other I/O error.
            if self._stop_evt.is_set():
                return
            self._on_status(f"error: accept: {exc}")
            return
        self._connected_evt.set()
        self._on_status(f"connected: {peer}")
        # Hand off to Worker.run; block on stop event.
        try:
            with Worker.run(
                cfg=self._cfg,
                watch_dir=self._watch_dir,
                science_stamp=self._science_stamp,
                bpm_good=self._bpm_good,
                tcs_socket=sock,
            ) as worker:
                with self._lock:
                    self._worker = worker
                self._on_status("running")
                self._stop_evt.wait()
        finally:
            with self._lock:
                self._worker = None
            with contextlib.suppress(OSError):
                sock.close()


@contextmanager
def run_autoguider(
    *,
    cfg: Config,
    watch_dir: str | Path,
    science_stamp: Stamp,
    bpm_good: np.ndarray,
    on_status: Callable[[str], None] | None = None,
) -> Iterator[AutoguiderRuntime]:
    """Synchronous context-managed wrapper around AutoguiderRuntime.

    Yields the runtime; cleanly stops on exit. Convenient for CLI:

        with run_autoguider(cfg=cfg, ...) as rt:
            signal.pause()
    """
    rt = AutoguiderRuntime(
        cfg=cfg,
        watch_dir=watch_dir,
        science_stamp=science_stamp,
        bpm_good=bpm_good,
        on_status=on_status,
    )
    rt.start()
    try:
        yield rt
    finally:
        rt.stop()
