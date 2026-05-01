"""Does watchdog fire on_closed when a file is fully written?

We want to know whether we can detect "Archon finished writing this FITS"
via an event-driven OS notification instead of a settle-timer poll.
On Linux this is inotify's IN_CLOSE_WRITE; on macOS it's kqueue's
NOTE_CLOSE_WRITE. macOS's default `Observer()` uses FSEvents, which does
NOT expose close-after-write — so on macOS we need to instantiate the
KqueueObserver explicitly.

This script tests both Kqueue and FSEvents observers on a temp directory
and reports which fire `on_closed` and when.

Usage:
    .venv/bin/python experiments/watchdog_close_event_test.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers.fsevents import FSEventsObserver
from watchdog.observers.kqueue import KqueueObserver

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "test" / "hen1764_001r.fits"


class LoggingHandler(FileSystemEventHandler):
    def __init__(self, label: str):
        self.label = label
        self.events: list[tuple[float, str, str]] = []
        self.start = time.monotonic()

    def _log(self, name: str, src: str) -> None:
        t = time.monotonic() - self.start
        print(f"  [{self.label:9s}] +{t:6.3f}s  {name:14s}  {Path(src).name}")
        self.events.append((t, name, src))

    def on_created(self, event):  self._log("on_created",  event.src_path)
    def on_modified(self, event): self._log("on_modified", event.src_path)
    def on_closed(self, event):   self._log("on_closed",   event.src_path)
    def on_moved(self, event):    self._log("on_moved",    event.src_path)
    def on_deleted(self, event):  self._log("on_deleted",  event.src_path)


def run_one(observer_cls, label: str, watchdir: Path) -> list:
    print(f"\n=== {label} ({observer_cls.__name__}) ===")
    handler = LoggingHandler(label)
    obs = observer_cls()
    obs.schedule(handler, str(watchdir), recursive=False)
    obs.start()

    time.sleep(0.5)  # let the observer settle

    target = watchdir / SOURCE.name
    print(f"  copying {SOURCE.name} ({SOURCE.stat().st_size / 1024:.0f} KB) "
          f"into watch dir...")
    t0 = time.monotonic()
    shutil.copy(str(SOURCE), str(target))
    elapsed_ms = (time.monotonic() - t0) * 1000
    print(f"  copy returned in {elapsed_ms:.1f} ms")

    # Wait for any trailing events
    time.sleep(2.0)

    obs.stop()
    obs.join()
    target.unlink(missing_ok=True)
    return handler.events


def main() -> int:
    if not SOURCE.exists():
        sys.exit(f"need {SOURCE} to exist; copy a sample frame first")

    print(f"Source: {SOURCE}  ({SOURCE.stat().st_size / 1024 / 1024:.1f} MB)")

    with tempfile.TemporaryDirectory() as td:
        watchdir = Path(td)
        print(f"Watching: {watchdir}")

        kq_events = run_one(KqueueObserver, "Kqueue",   watchdir)
        fs_events = run_one(FSEventsObserver, "FSEvents", watchdir)

    print("\n=== SUMMARY ===")
    for label, events in [("Kqueue", kq_events), ("FSEvents", fs_events)]:
        kinds = [name for _, name, _ in events]
        n_close = sum(1 for k in kinds if k == "on_closed")
        n_create = sum(1 for k in kinds if k == "on_created")
        n_modify = sum(1 for k in kinds if k == "on_modified")
        last_event = events[-1][:2] if events else ("-", "-")
        print(f"  {label:9s}  on_created={n_create}  on_modified={n_modify}  "
              f"on_closed={n_close}  last={last_event}")

    print("\nVerdict:")
    if any(name == "on_closed" for _, name, _ in kq_events):
        last_close = max(t for t, name, _ in kq_events if name == "on_closed")
        print(f"  ✓ KqueueObserver fires on_closed (last at +{last_close:.3f}s).")
        print("    -> use KqueueObserver on macOS for atomic completion detection.")
    else:
        print("  ✗ KqueueObserver did NOT fire on_closed.")
        print("    -> need a settle-timer fallback.")
    if any(name == "on_closed" for _, name, _ in fs_events):
        print("  ! FSEventsObserver also fires on_closed (unexpected).")
    else:
        print("  (expected) FSEventsObserver does not fire on_closed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
