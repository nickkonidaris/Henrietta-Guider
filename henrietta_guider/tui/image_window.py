"""matplotlib image side-window — runs in a separate subprocess.

macOS Tk + AppKit require Cocoa to live on the process main thread;
the textual TUI already owns the main thread, so we cannot run Tk in
a side thread (it crashes with NSInternalInconsistency). We use plain
`subprocess.Popen` + a length-framed pickle stream over stdin rather
than `multiprocessing.Queue`, because Python 3.14's
`multiprocessing.resource_tracker` has a fd-handling regression on
macOS that fails with "bad value(s) in fds_to_keep" before the queue
is even ready.

Lifecycle: parent constructs `ImageWindow()`, calls `.start()` to
spawn the subprocess, pushes numpy arrays via `.push_image(arr)`, and
calls `.stop()` on shutdown. The subprocess exits when the user closes
the window or when `.stop()` closes stdin, at which point `.available`
returns False and `.push_image` silently no-ops.

Frames are pickled and length-framed (4-byte network-order size, then
size bytes of pickle). For ~256x2048 float32 arrays at 1 Hz this is a
few MB/s — well within a pipe's bandwidth.
"""

from __future__ import annotations

import contextlib
import logging
import os
import pickle
import queue
import struct
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

log = logging.getLogger(__name__)


_SUBPROCESS_MODULE = "henrietta_guider.tui._image_window_subprocess"


class ImageWindow:
    """TUI-side handle for the matplotlib subprocess.

    `available` flips to False once the user closes the matplotlib
    window, the subprocess exits, or it never started. Push calls
    thereafter are silent no-ops.
    """

    SCIENCE_COLOR = "#E63946"
    COMPARISON_COLOR = "#5BC0EB"
    ROTATION_COLOR = "#9D4EDD"

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._stderr_file = None  # held open for the subprocess's lifetime
        # Pickled-frame outbound queue + writer thread. The writer thread
        # owns the synchronous stdin.write so the textual main thread
        # never blocks behind a backed-up pipe (~64 KB pipe buffer on
        # macOS vs. ~16 MB frames). maxsize=2 keeps memory bounded; if
        # we're behind, drop the oldest pending pickle.
        self._outbox: queue.Queue[bytes] = queue.Queue(maxsize=2)
        self._writer_thread: threading.Thread | None = None
        self._writer_stop = threading.Event()

    def start(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return  # already running
        # Stash subprocess stderr to a temp file so failures don't get
        # lost in DEVNULL but also don't corrupt the textual TUI's
        # stderr.
        log_path = Path(tempfile.gettempdir()) / f"henrietta_image_window_{os.getpid()}.log"
        try:
            self._stderr_file = open(log_path, "wb")  # noqa: SIM115
        except OSError:
            self._stderr_file = subprocess.DEVNULL  # type: ignore[assignment]
            log_path = None
        try:
            self._proc = subprocess.Popen(
                [sys.executable, "-m", _SUBPROCESS_MODULE],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=self._stderr_file,
                close_fds=True,
            )
        except OSError as exc:
            log.warning("Image window subprocess failed to start: %s", exc)
            self._proc = None
            return
        if log_path is not None:
            log.info(
                "Image window subprocess started: pid=%s (stderr -> %s)",
                self._proc.pid,
                log_path,
            )
        else:
            log.info("Image window subprocess started: pid=%s", self._proc.pid)
        self._writer_stop.clear()
        self._writer_thread = threading.Thread(
            target=self._writer_loop,
            daemon=True,
            name="image-window-writer",
        )
        self._writer_thread.start()

    def _writer_loop(self) -> None:
        """Drain the outbox and write to subprocess stdin. Runs in a
        daemon thread so the synchronous pipe write never blocks the
        textual main loop."""
        while not self._writer_stop.is_set():
            try:
                payload = self._outbox.get(timeout=0.2)
            except queue.Empty:
                continue
            if self._proc is None or self._proc.stdin is None or self._proc.poll() is not None:
                return
            try:
                self._proc.stdin.write(payload)
                self._proc.stdin.flush()
            except BrokenPipeError, OSError:
                # User closed the matplotlib window; subsequent pushes
                # will short-circuit on .available.
                return

    @property
    def available(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def push_image(self, image) -> None:
        """Push the latest guide image to the subprocess.

        Non-blocking: pickles on the caller's thread, then hands the
        bytes off to a writer thread via a small bounded queue.
        Drops the oldest pending pickle when the queue is full.
        Silent no-op when the subprocess is not running.
        """
        if not self.available:
            return
        try:
            data = pickle.dumps(image, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception:
            return
        payload = struct.pack("!I", len(data)) + data
        # Drop the oldest if we're already at capacity, then enqueue.
        try:
            self._outbox.put_nowait(payload)
        except queue.Full:
            with contextlib.suppress(queue.Empty):
                self._outbox.get_nowait()
            with contextlib.suppress(queue.Full):
                self._outbox.put_nowait(payload)

    def stop(self, join_timeout_s: float = 2.0) -> None:
        """Tear down the subprocess. Idempotent."""
        # Stop the writer thread first so it doesn't race against
        # closing stdin under it.
        self._writer_stop.set()
        if self._writer_thread is not None:
            self._writer_thread.join(timeout=1.0)
            self._writer_thread = None
        if self._proc is None:
            return
        with contextlib.suppress(Exception):
            if self._proc.stdin is not None:
                self._proc.stdin.close()
        try:
            self._proc.wait(timeout=join_timeout_s)
        except subprocess.TimeoutExpired:
            self._proc.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self._proc.wait(timeout=1.0)
            if self._proc.poll() is None:
                self._proc.kill()
        self._proc = None
        if self._stderr_file is not None and self._stderr_file != subprocess.DEVNULL:
            with contextlib.suppress(Exception):
                self._stderr_file.close()
        self._stderr_file = None
