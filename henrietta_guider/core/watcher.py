"""Filesystem watcher + settle timer + dual-queue routing.

watchdog Observer subscribes to the configured directory. For each
.fits file event, a 0.2 s settle timer per path is reset; when the
timer fires, the file is opened with astropy.io.fits and the parsed
data is pushed onto:

  hen NNNN _sssr .fits   ->  sutr_queue   (frame_number, sutr_number, raw_read, path)
  hen NNNN .fits         ->  slope_queue  (frame_number, path)

Anything else is logged at DEBUG and dropped.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
from contextlib import contextmanager
from pathlib import Path

import numpy as np
from astropy.io import fits
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

log = logging.getLogger(__name__)

_SUTR_RE = re.compile(r"^hen(\d{4})_(\d{3})r\.fits$")
_SLOPE_RE = re.compile(r"^hen(\d{4})\.fits$")


class Watcher:
    def __init__(self, settle_s: float = 0.2) -> None:
        self.settle_s = settle_s
        self.sutr_queue: queue.Queue = queue.Queue()
        self.slope_queue: queue.Queue = queue.Queue()
        self._observer: Observer | None = None
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()
        # Snapshot of file basenames present in the watch dir at start
        # time. macOS FSEvents will sometimes replay events for these on
        # observer start; the SanityChecker downstream then trips on
        # out-of-order frame numbers and discards everything. We ignore
        # any path whose basename is in this snapshot.
        self._preexisting: set[str] = set()

    @classmethod
    @contextmanager
    def start(cls, watch_dir: str | Path, settle_s: float = 0.2):
        """Context-managed constructor; start_unmanaged + stop manual."""
        w = cls(settle_s=settle_s)
        w.start_unmanaged(watch_dir)
        try:
            yield w
        finally:
            w.stop()

    def start_unmanaged(self, watch_dir: str | Path) -> None:
        """Start the underlying observer without a context manager.

        Public; used by Worker.run which wants to compose its own setup
        and teardown around the watcher. Snapshots pre-existing files
        so FSEvents replays don't backflow stale frames into the queue.
        """
        wd = Path(watch_dir).expanduser()
        try:
            self._preexisting = {p.name for p in wd.iterdir()}
        except OSError:
            self._preexisting = set()
        handler = _Handler(self)
        obs = Observer()
        obs.schedule(handler, str(wd), recursive=False)
        obs.start()
        self._observer = obs

    def stop(self) -> None:
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=2.0)
            finally:
                self._observer = None
        with self._lock:
            for t in list(self._timers.values()):
                t.cancel()
            self._timers.clear()

    # Called from the watchdog thread.
    def _bump_settle(self, path: str) -> None:
        with self._lock:
            existing = self._timers.pop(path, None)
            if existing is not None:
                existing.cancel()
            t = threading.Timer(self.settle_s, self._consume, args=(path,))
            t.daemon = True
            self._timers[path] = t
            t.start()

    # Called from the timer thread when settle expires.
    def _consume(self, path: str) -> None:
        with self._lock:
            self._timers.pop(path, None)
        name = Path(path).name

        # Skip files that were already in the watch dir at start time.
        # FSEvents on macOS occasionally replays events for them during
        # observer startup; processing those would drag stale frame
        # numbers into the SanityChecker's state and cause every new
        # frame to be discarded as "frame_backwards".
        if name in self._preexisting:
            log.debug("watcher: ignoring pre-existing file %s", name)
            return

        m_sutr = _SUTR_RE.match(name)
        m_slope = _SLOPE_RE.match(name)
        if not (m_sutr or m_slope):
            log.debug("watcher: ignored unmatched filename %s", name)
            return

        try:
            with fits.open(path) as hdul:
                data = np.asarray(hdul[0].data, dtype=np.float32)
        except Exception as exc:
            log.warning("watcher: failed to open %s: %s", path, exc)
            return

        if m_sutr:
            frame = int(m_sutr.group(1))
            sutr = int(m_sutr.group(2))
            # Include the path so the worker can persist frame_path and
            # later read FITS-header keywords (HA / Dec / OBJECT for
            # target-switch detection) without re-opening from scratch.
            self.sutr_queue.put((frame, sutr, data, path))
            return

        assert m_slope is not None
        frame = int(m_slope.group(1))
        self.slope_queue.put((frame, path))


class _Handler(FileSystemEventHandler):
    def __init__(self, watcher: Watcher) -> None:
        self.watcher = watcher

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory or not event.src_path.endswith(".fits"):
            return
        self.watcher._bump_settle(event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory or not event.src_path.endswith(".fits"):
            return
        self.watcher._bump_settle(event.src_path)
