"""matplotlib image side-window — runs in a separate process.

macOS Tk + AppKit require Cocoa to live on the process main thread;
the textual TUI already owns the main thread, so we cannot run Tk in
a side thread (we tried — it crashes with NSInternalInconsistency).
Spawning a subprocess gives matplotlib its own main thread.

Lifecycle: parent constructs `ImageWindow()`, calls `.start()` to
spawn the subprocess, pushes numpy arrays via `.push_image(arr)`,
and calls `.stop()` on shutdown. The subprocess exits when the user
closes the window (or when stop() is called), at which point
`.available` returns False and `.push_image` silently no-ops.

Communication is a single `multiprocessing.Queue[np.ndarray]` with a
small bound — frames are dropped on the floor if the GUI is paused
or slow. Image data is pickled across the process boundary; for
~256×2048 float32 arrays at 1 Hz this is fine (~2 MB/frame, 2 MB/s).
"""

from __future__ import annotations

import contextlib
import logging
import multiprocessing as mp

log = logging.getLogger(__name__)


def _subprocess_main(image_queue, stop_event) -> None:
    """Subprocess entry point. Imports matplotlib in the child only so
    the parent's TUI stays decoupled from any GUI framework."""
    # Lazy imports — these run in the child process only.
    import matplotlib

    matplotlib.use("TkAgg")
    import matplotlib.pyplot as plt
    from astropy.visualization import ZScaleInterval

    zs = ZScaleInterval()
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.set_facecolor("#000")
    with contextlib.suppress(Exception):
        fig.canvas.manager.set_window_title("Henrietta — guide image")

    state = {"img_artist": None}

    def poll() -> None:
        if stop_event.is_set():
            plt.close(fig)
            return
        # Drain whatever's queued so we always render the freshest frame.
        latest = None
        while True:
            try:
                latest = image_queue.get_nowait()
            except Exception:
                break
        if latest is not None:
            try:
                vlo, vhi = zs.get_limits(latest)
            except Exception:
                vlo, vhi = float(latest.min()), float(latest.max())
            if state["img_artist"] is None:
                state["img_artist"] = ax.imshow(
                    latest,
                    origin="lower",
                    cmap="viridis",
                    vmin=vlo,
                    vmax=vhi,
                    interpolation="nearest",
                    aspect="auto",
                )
            else:
                state["img_artist"].set_data(latest)
                state["img_artist"].set_clim(vmin=vlo, vmax=vhi)
            fig.canvas.draw_idle()
        # Window may have been closed between drain and re-arm.
        with contextlib.suppress(Exception):
            fig.canvas.manager.window.after(200, poll)

    with contextlib.suppress(Exception):
        fig.canvas.manager.window.after(200, poll)
    plt.show()  # blocks until the window is closed


class ImageWindow:
    """TUI-side handle for the matplotlib subprocess.

    `available` flips to False once the user closes the matplotlib
    window (or never opens because the subprocess failed to import a
    backend). Push calls thereafter are silent no-ops.
    """

    SCIENCE_COLOR = "#E63946"
    COMPARISON_COLOR = "#5BC0EB"
    ROTATION_COLOR = "#9D4EDD"

    def __init__(self) -> None:
        # 'spawn' is required on macOS and is the future default on
        # Linux. It re-imports the module in the child so the entry
        # point must be importable (it is — see _subprocess_main above).
        self._ctx = mp.get_context("spawn")
        self._proc: mp.Process | None = None
        self._image_queue = self._ctx.Queue(maxsize=4)
        self._stop_event = self._ctx.Event()

    def start(self) -> None:
        if self._proc is not None and self._proc.is_alive():
            return  # already started
        self._proc = self._ctx.Process(
            target=_subprocess_main,
            args=(self._image_queue, self._stop_event),
            daemon=True,
            name="henrietta-image-window",
        )
        self._proc.start()
        log.info("Image window subprocess started: pid=%s", self._proc.pid)

    @property
    def available(self) -> bool:
        return self._proc is not None and self._proc.is_alive()

    def push_image(self, image) -> None:
        """Worker (or demo feed) calls this with each new guide image.
        Silent no-op when the subprocess is not running. Drops frames
        when the queue is full (GUI paused / slow).
        """
        if not self.available:
            return
        with contextlib.suppress(Exception):
            self._image_queue.put_nowait(image)

    def stop(self, join_timeout_s: float = 2.0) -> None:
        """Tear down the subprocess. Idempotent."""
        if self._stop_event is not None:
            self._stop_event.set()
        if self._proc is not None:
            self._proc.join(timeout=join_timeout_s)
            if self._proc.is_alive():
                self._proc.terminate()
                self._proc.join(timeout=1.0)
            self._proc = None
