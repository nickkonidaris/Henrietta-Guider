"""matplotlib side-window for the live guide image.

Runs on its own thread (separate from the textual main loop). Owns:

  - a Tk root + matplotlib FigureCanvasTkAgg
  - a NavigationToolbar2Tk (zoom / pan / home)
  - draggable RectangleSelector overlays for the science / comparison
    / rotation stamps
  - a queue.Queue[np.ndarray] of incoming guide images
  - a queue.Queue[Stamp_update] of stamp-geometry edits the worker
    needs to pick up

The window is OPTIONAL — if Tk fails to initialise (no display), the
window object falls back to a no-op and the TUI's status bar reflects
that. The TUI never imports matplotlib directly; it imports this
module which lazily loads matplotlib in __init__.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)


class ImageWindow:
    SCIENCE_COLOR = "#E63946"
    COMPARISON_COLOR = "#5BC0EB"
    ROTATION_COLOR = "#9D4EDD"

    def __init__(self, on_stamp_changed: Callable | None = None) -> None:
        self.image_queue: queue.Queue = queue.Queue(maxsize=8)
        self.on_stamp_changed = on_stamp_changed
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._available = False  # set True if we successfully imported Tk

    def start(self) -> None:
        """Start the side thread. Returns even if no display is
        available; in that case the TUI surfaces 'image: unavailable'."""
        try:
            import matplotlib

            matplotlib.use("TkAgg")
            import tkinter  # noqa: F401  -- probe for display
        except Exception as exc:
            log.warning("Image side-window unavailable: %s", exc)
            return
        self._available = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    @property
    def available(self) -> bool:
        return self._available

    def push_image(self, image) -> None:
        """Worker thread calls this with each new guide image."""
        if not self._available:
            return
        # Drop frames if the user blocked the GUI.
        with contextlib.suppress(queue.Full):
            self.image_queue.put_nowait(image)

    def _run(self) -> None:
        # TODO(7.8): The locals below (zs, fig, ax, img_artist) are
        # scaffolding for the full RectangleSelector + ZScale + template
        # thumbnail wire-up landing in Task 7.8. Suppressed F841 until
        # then — see spec §9 (Image window).
        import matplotlib.pyplot as plt
        from astropy.visualization import ZScaleInterval

        zs = ZScaleInterval()  # noqa: F841
        fig, ax = plt.subplots(figsize=(8, 6))  # noqa: F841
        ax.set_facecolor("#000")
        img_artist = None  # noqa: F841
        # See spec §9 — Image window. Full RectangleSelector wiring +
        # template thumbnail inset is finished in Task 7.8.
        plt.show()  # blocks; closes when user closes the window
