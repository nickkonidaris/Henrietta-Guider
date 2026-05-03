"""Image-window subprocess entry point.

Read pickled message envelopes from stdin (length-framed: 4-byte
network-order size, then `size` bytes of pickle), update a matplotlib
figure each tick, exit when the parent closes stdin or the user closes
the window.

Message envelope shape (a pickled dict):

    {"type": "image",  "image":  np.ndarray}
    {"type": "stamps", "stamps": [{"id": int, "x_min": int, "y_lo": int,
                                    "x_max": int, "y_hi": int,
                                    "color": str, "label": str}, ...]}

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
_INCOMING: queue.Queue = queue.Queue(maxsize=8)


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
    import matplotlib.patches as mpatches
    import matplotlib.pyplot as plt
    from astropy.visualization import ZScaleInterval

    zs = ZScaleInterval()
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.set_facecolor("#000")
    with contextlib.suppress(Exception):
        fig.canvas.manager.set_window_title("Henrietta — guide image")

    state: dict = {
        "artist": None,
        "rect_patches": [],  # list of Rectangle patches we own
        "label_artists": [],  # list of Text artists we own
    }

    def _draw_stamps(stamps: list[dict]) -> None:
        # Remove previous overlays.
        for p in state["rect_patches"]:
            with contextlib.suppress(Exception):
                p.remove()
        for t in state["label_artists"]:
            with contextlib.suppress(Exception):
                t.remove()
        state["rect_patches"] = []
        state["label_artists"] = []
        for s in stamps:
            x_min = float(s["x_min"])
            y_lo = float(s["y_lo"])
            width = float(s["x_max"]) - x_min
            height = float(s["y_hi"]) - y_lo
            color = s.get("color", "#FFFFFF")
            label = s.get("label", str(s.get("id", "")))
            # Auto sky bands: outer 1/6 of the width on each side, matching
            # core/sky.py (per-row outer-1/6 median). Drawn first so the
            # main outline sits on top.
            edge = max(1.0, width // 6)
            for sx in (x_min, x_min + width - edge):
                sky = mpatches.Rectangle(
                    (sx, y_lo),
                    edge,
                    height,
                    ec="none",
                    fc=color,
                    alpha=0.18,
                )
                ax.add_patch(sky)
                state["rect_patches"].append(sky)
            rect = mpatches.Rectangle(
                (x_min, y_lo),
                width,
                height,
                ec=color,
                fc="none",
                lw=1.5,
            )
            ax.add_patch(rect)
            state["rect_patches"].append(rect)
            txt = ax.text(
                x_min,
                y_lo - 12,
                label,
                color=color,
                fontsize=9,
                fontweight="bold",
            )
            state["label_artists"].append(txt)

    def poll() -> None:
        latest_image = None
        latest_stamps = None
        sentinel = False
        while True:
            try:
                v = _INCOMING.get_nowait()
            except queue.Empty:
                break
            if v is _SENTINEL:
                sentinel = True
                continue
            if not isinstance(v, dict):
                continue
            t = v.get("type")
            if t == "image":
                latest_image = v.get("image")
            elif t == "stamps":
                latest_stamps = v.get("stamps") or []

        redraw = False
        if latest_image is not None:
            try:
                vlo, vhi = zs.get_limits(latest_image)
            except Exception:
                vlo = float(latest_image.min())
                vhi = float(latest_image.max())
            if state["artist"] is None:
                state["artist"] = ax.imshow(
                    latest_image,
                    origin="lower",
                    cmap="viridis",
                    vmin=vlo,
                    vmax=vhi,
                    interpolation="nearest",
                    aspect="auto",
                )
            else:
                state["artist"].set_data(latest_image)
                state["artist"].set_clim(vmin=vlo, vmax=vhi)
            redraw = True
        if latest_stamps is not None:
            _draw_stamps(latest_stamps)
            redraw = True
        if redraw:
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
