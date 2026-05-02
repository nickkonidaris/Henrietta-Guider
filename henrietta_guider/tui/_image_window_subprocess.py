"""Image-window subprocess entry point.

Read pickled numpy arrays from stdin (length-framed: 4-byte network-
order size, then `size` bytes of pickle), update a matplotlib figure
each tick, exit when the parent closes stdin or the user closes the
window.

Run as: `python -m henrietta_guider.tui._image_window_subprocess`.
"""

from __future__ import annotations

import contextlib
import pickle
import queue
import struct
import sys
import threading

# Lazy imports of GUI deps happen inside main() so import-time cost
# is only paid when the subprocess actually launches.

_SENTINEL = object()
_INCOMING: queue.Queue = queue.Queue(maxsize=4)


def _reader_thread() -> None:
    """Read length-framed pickles from stdin until EOF.

    Runs in a daemon thread inside the subprocess. The Tk main thread
    polls _INCOMING via fig.canvas.manager.window.after(...).
    """
    stream = sys.stdin.buffer
    while True:
        header = stream.read(4)
        if not header or len(header) < 4:
            _INCOMING.put(_SENTINEL)
            return
        size = struct.unpack("!I", header)[0]
        buf = bytearray()
        while len(buf) < size:
            chunk = stream.read(size - len(buf))
            if not chunk:
                _INCOMING.put(_SENTINEL)
                return
            buf.extend(chunk)
        try:
            obj = pickle.loads(bytes(buf))
        except Exception:
            continue
        with contextlib.suppress(queue.Full):
            _INCOMING.put_nowait(obj)


def main() -> int:
    import matplotlib

    matplotlib.use("TkAgg")
    import matplotlib.pyplot as plt
    from astropy.visualization import ZScaleInterval

    zs = ZScaleInterval()
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.set_facecolor("#000")
    with contextlib.suppress(Exception):
        fig.canvas.manager.set_window_title("Henrietta — guide image")

    state = {"artist": None}

    def poll() -> None:
        latest = None
        sentinel = False
        while True:
            try:
                v = _INCOMING.get_nowait()
            except queue.Empty:
                break
            if v is _SENTINEL:
                sentinel = True
            else:
                latest = v
        if latest is not None:
            try:
                vlo, vhi = zs.get_limits(latest)
            except Exception:
                vlo, vhi = float(latest.min()), float(latest.max())
            if state["artist"] is None:
                state["artist"] = ax.imshow(
                    latest,
                    origin="lower",
                    cmap="viridis",
                    vmin=vlo,
                    vmax=vhi,
                    interpolation="nearest",
                    aspect="auto",
                )
            else:
                state["artist"].set_data(latest)
                state["artist"].set_clim(vmin=vlo, vmax=vhi)
            fig.canvas.draw_idle()
        if sentinel:
            with contextlib.suppress(Exception):
                plt.close(fig)
            return
        with contextlib.suppress(Exception):
            fig.canvas.manager.window.after(200, poll)

    threading.Thread(target=_reader_thread, daemon=True).start()
    with contextlib.suppress(Exception):
        fig.canvas.manager.window.after(200, poll)
    plt.show()  # blocks until window is closed
    return 0


if __name__ == "__main__":
    sys.exit(main())
