"""Vi-style command palette for the operator TUI.

Bound to `:` on the App. Lets the operator type short commands to
position the science / rotation / reference stamp boxes (drawn as
overlays on the matplotlib image side-window).

Supported syntax:

    :1 x_min y_lo x_max y_hi      set science stamp (red)
    :2 x_min y_lo x_max y_hi      set rotation stamp (purple)
    :3 x_min y_lo x_max y_hi      set reference stamp (blue)
    :?                             list commands in a modal window

Coordinates are detector pixels; the half-open interval
[x_min, x_max) × [y_lo, y_hi) matches the stamp convention used by
the rest of the codebase.

The command result type is a small dataclass so the App's handler
can switch on it cleanly.
"""

from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static

# Stamp id mapping the user expects (1-indexed, matches the natural
# operator vocabulary on the bring-up page):
#   1 = science (the box that drives RA/Dec corrections)
#   2 = rotation (the box that drives the field-rotation derivation)
#   3 = reference / comparison (a second drift-monitor box)
STAMP_LABELS: dict[int, str] = {1: "science", 2: "rotation", 3: "reference"}
STAMP_COLORS: dict[int, str] = {1: "#E63946", 2: "#9D4EDD", 3: "#5BC0EB"}


@dataclass(frozen=True)
class SetStamp:
    """Result of `:N x_min y_lo x_max y_hi`."""

    n: int  # 1, 2, or 3
    x_min: int
    y_lo: int
    x_max: int
    y_hi: int


@dataclass(frozen=True)
class ClearStamps:
    """Result of `:clear` (n=None) or `:clear N`."""

    n: int | None  # None = all


@dataclass(frozen=True)
class SetPa:
    """Result of `:pa <deg>` — sky-frame position angle in degrees."""

    deg: float


@dataclass(frozen=True)
class ShowHelp:
    """Result of `:?`."""


@dataclass(frozen=True)
class ParseError:
    """Result when the typed command can't be parsed; carries a
    short message the App can flash via App.notify(...)."""

    message: str


def parse_command(
    text: str,
) -> SetStamp | ClearStamps | SetPa | ShowHelp | ParseError:
    """Parse a typed command string (no leading colon).

    Whitespace and commas are interchangeable. Empty input is treated
    as a parse error (caller dismisses without action).
    """
    s = text.strip()
    if not s:
        return ParseError("empty command")
    if s == "?":
        return ShowHelp()
    if s == "clear" or s.startswith("clear "):
        rest = s[len("clear") :].strip()
        if not rest:
            return ClearStamps(n=None)
        try:
            n = int(rest)
        except ValueError:
            return ParseError(f"`clear` takes nothing or 1/2/3 (got `{rest}`)")
        if n not in STAMP_LABELS:
            return ParseError(f"`clear N`: N must be 1, 2, or 3 (got {n})")
        return ClearStamps(n=n)
    if s.startswith("pa"):
        rest = s[len("pa") :].strip()
        if not rest:
            return ParseError("`pa` needs an angle in degrees, e.g. `:pa 35`")
        try:
            deg = float(rest)
        except ValueError:
            return ParseError(f"`pa` takes a number of degrees (got `{rest}`)")
        return SetPa(deg=deg)
    parts = s.replace(",", " ").split()
    if len(parts) != 5:
        return ParseError(f"expected `N x_min y_lo x_max y_hi` (5 values) — got {len(parts)}")
    try:
        n, x_min, y_lo, x_max, y_hi = (int(p) for p in parts)
    except ValueError:
        return ParseError("all values must be integers")
    if n not in STAMP_LABELS:
        return ParseError(f"stamp id must be 1, 2, or 3 (got {n})")
    if x_min >= x_max:
        return ParseError(f"x_min ({x_min}) must be less than x_max ({x_max})")
    if y_lo >= y_hi:
        return ParseError(f"y_lo ({y_lo}) must be less than y_hi ({y_hi})")
    return SetStamp(n=n, x_min=x_min, y_lo=y_lo, x_max=x_max, y_hi=y_hi)


class CommandPrompt(ModalScreen[str | None]):
    """Single-line input pinned to the bottom of the screen.

    Dismisses with the typed string on Enter (caller parses), or with
    None on Escape. Up/Down walk command history (most recent last).
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("up", "history_prev", show=False),
        Binding("down", "history_next", show=False),
    ]

    DEFAULT_CSS = """
    CommandPrompt {
        align: center bottom;
    }
    CommandPrompt #wrap {
        width: 100%;
        background: #1F2937;
        padding: 0 1;
    }
    CommandPrompt Input {
        background: #1F2937;
    }
    CommandPrompt #hint {
        color: #9CA3AF;
        padding: 0 1;
    }
    """

    def __init__(self, history: list[str] | None = None) -> None:
        super().__init__()
        # Caller passes a snapshot of the App's history (most recent
        # last). _cursor=None means "at the live prompt"; stepping back
        # with Up walks toward 0.
        self._history: list[str] = list(history or [])
        self._cursor: int | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="wrap"):
            yield Static(":N x_min y_lo x_max y_hi   |   :?  for help", id="hint")
            yield Input(placeholder="command…", id="cmd")

    def on_mount(self) -> None:
        self.query_one("#cmd", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)

    # --- history navigation -------------------------------------------

    def action_history_prev(self) -> None:
        if not self._history:
            return
        if self._cursor is None:
            self._cursor = len(self._history) - 1
        elif self._cursor > 0:
            self._cursor -= 1
        self._set_input(self._history[self._cursor])

    def action_history_next(self) -> None:
        if self._cursor is None:
            return
        if self._cursor < len(self._history) - 1:
            self._cursor += 1
            self._set_input(self._history[self._cursor])
        else:
            # Past the newest -> back to a blank live prompt.
            self._cursor = None
            self._set_input("")

    def _set_input(self, value: str) -> None:
        inp = self.query_one("#cmd", Input)
        inp.value = value
        inp.cursor_position = len(value)


class CommandHelp(ModalScreen):
    """Read-only modal listing the supported commands."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close"),
        Binding("enter", "close", "Close"),
    ]

    DEFAULT_CSS = """
    CommandHelp { align: center middle; }
    CommandHelp Vertical {
        width: 70;
        height: auto;
        padding: 1 2;
        background: #20242C;
    }
    CommandHelp Static.title {
        color: #F9FAFB;
        text-style: bold;
    }
    CommandHelp Static.row {
        padding: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Commands", classes="title")
            yield Static("", classes="row")
            yield Static(
                ":1 x_min y_lo x_max y_hi    set science stamp   (red)",
                classes="row",
            )
            yield Static(
                ":2 x_min y_lo x_max y_hi    set rotation stamp  (purple)",
                classes="row",
            )
            yield Static(
                ":3 x_min y_lo x_max y_hi    set reference stamp (blue)",
                classes="row",
            )
            yield Static(":clear                        clear all stamps", classes="row")
            yield Static(
                ":clear N                      clear stamp N (1/2/3)",
                classes="row",
            )
            yield Static(
                ":pa <deg>                     set position angle (sky frame)",
                classes="row",
            )
            yield Static(":?                            this help", classes="row")
            yield Static("", classes="row")
            yield Static(
                "Coordinates are detector pixels. Sky-band overlays "
                "(outer 1/6 of the X width) are auto-drawn from each stamp.",
                classes="row",
            )
            yield Static(
                "If a box is already set, you must `:clear N` before "
                "`:N` will accept new coordinates.",
                classes="row",
            )
            yield Static(
                "Stamps overlay the live matplotlib image only — the "
                "worker's reducer still uses the science stamp it was "
                "constructed from.",
                classes="row",
            )
            yield Static("", classes="row")
            yield Static("Esc / q / Enter to dismiss.", classes="row")

    def action_close(self) -> None:
        self.dismiss()
