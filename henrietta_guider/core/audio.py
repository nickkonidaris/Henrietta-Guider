"""Non-blocking audio + speech dispatch for alerts.

Thin wrappers around platform CLIs:
  macOS  -> afplay / say
  Linux  -> paplay or aplay (sound) / espeak (speech)

Failures (binary missing, file not found) log WARNING and return
False; callers may then fall back to Tk's widget.bell(). Audio is
fire-and-forget: a slow subsystem can never stall the worker thread.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)
_warned: set[str] = set()


def _spawn(argv: list[str]) -> subprocess.Popen | None:
    """Production spawn helper; tests monkeypatch this."""
    try:
        return subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError as exc:
        msg = f"audio binary missing: {argv[0]}"
        if msg not in _warned:
            log.warning("%s (%s)", msg, exc)
            _warned.add(msg)
        return None


def play_sound(path: str | Path, enabled: bool = True) -> bool:
    """Play a sound file. Returns True if a subprocess was spawned."""
    if not enabled:
        return False
    p = str(Path(path).expanduser())
    if sys.platform == "darwin":
        argv = ["afplay", p]
    elif sys.platform.startswith("linux"):
        binary = "paplay" if shutil.which("paplay") else "aplay"
        argv = [binary, p]
    else:
        log.warning("play_sound: unsupported platform %s", sys.platform)
        return False
    _spawn(argv)
    return True


def speak(phrase: str, enabled: bool = True) -> bool:
    """Speak a phrase via the OS speech synthesiser."""
    if not enabled:
        return False
    if sys.platform == "darwin":
        argv = ["say", phrase]
    elif sys.platform.startswith("linux"):
        argv = ["espeak", phrase]
    else:
        log.warning("speak: unsupported platform %s", sys.platform)
        return False
    _spawn(argv)
    return True
