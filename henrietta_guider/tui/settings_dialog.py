"""Settings modal: tabbed config editor with TOML round-trip.

Per spec §8 (config schema) and §9 (operator interface). Saving
persists the TOML via core.config.save_config; loop/quality/pacing
edits do NOT hot-reload into a running worker (v1 limitation, see
plan §"Plan complete" deferrals). Display-only edits (audio_alerts,
audio_alert_sound) take effect on the next event since the TUI reads
them live.
"""

from __future__ import annotations

import copy
import dataclasses
from collections.abc import Callable
from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static, TabbedContent, TabPane

from henrietta_guider.core.config import Config, save_config

# Sections we expose, in display order.
_SECTIONS: tuple[str, ...] = (
    "loop",
    "quality",
    "reduction",
    "files",
    "tcs",
    "detector",
    "display",
)


def _coerce(value_str: str, current_value: Any) -> Any:
    """Coerce a textual Input string back into the type of the existing
    config field. Booleans accept '1/0', 'true/false', 'yes/no'.
    """
    if isinstance(current_value, bool):
        return value_str.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(current_value, int):
        return int(value_str)
    if isinstance(current_value, float):
        return float(value_str)
    if current_value is None:
        return value_str if value_str else None
    return value_str  # str or unknown


def collect_values(
    cfg: Config,
    inputs: dict[tuple[str, str], str],
) -> Config:
    """Pure: build a new Config replacing fields named (section, key)
    with the coerced values from `inputs`. Untouched fields are kept.
    Raises ValueError on coercion failure.

    Uses copy.deepcopy so the caller's cfg (and its nested sub-dataclasses)
    are not mutated; dataclasses.replace would only shallow-copy the outer
    Config, leaving inner sections shared by reference.
    """
    new_cfg = copy.deepcopy(cfg)
    for (section, key), s in inputs.items():
        sub = getattr(new_cfg, section)
        current = getattr(sub, key)
        try:
            coerced = _coerce(s, current)
        except (ValueError, TypeError) as e:
            raise ValueError(f"{section}.{key}: {e}") from e
        setattr(sub, key, coerced)
    return new_cfg


class SettingsDialog(ModalScreen):
    """Tabbed modal for editing config. Save persists; restart to apply."""

    DEFAULT_CSS = """
    SettingsDialog { align: center middle; }
    SettingsDialog Vertical { width: 100; height: 30;
                              padding: 1; background: #20242C; }
    SettingsDialog #status { color: #FBBF24; padding: 1; }
    SettingsDialog Input { margin: 0 1; }
    """

    def __init__(
        self,
        cfg: Config,
        save_path: str | Path,
        on_saved: Callable[[Config], None],
    ) -> None:
        super().__init__()
        self.cfg = cfg
        self.save_path = save_path
        self._on_saved = on_saved
        self._inputs: dict[tuple[str, str], Input] = {}
        self._status = Static("", id="status")

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Settings — autoguider configuration")
            with TabbedContent():
                for section in _SECTIONS:
                    sub = getattr(self.cfg, section)
                    with TabPane(section, id=f"tab_{section}"):
                        yield from self._build_tab_inputs(section, sub)
            yield self._status
            yield Button("Save", id="save")
            yield Button("Cancel", id="cancel")

    def _build_tab_inputs(self, section: str, sub: Any):
        for fld in dataclasses.fields(sub):
            current = getattr(sub, fld.name)
            label = f"{fld.name}"
            yield Static(label)
            inp = Input(
                value=str(current) if current is not None else "",
                id=f"in_{section}_{fld.name}",
            )
            self._inputs[(section, fld.name)] = inp
            yield inp

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self._save()
        elif event.button.id == "cancel":
            self.dismiss()

    def _save(self) -> None:
        try:
            inputs = {k: v.value for k, v in self._inputs.items()}
            new_cfg = collect_values(self.cfg, inputs)
        except ValueError as exc:
            self._status.update(f"Invalid: {exc}")
            return
        save_config(new_cfg, self.save_path)
        self._on_saved(new_cfg)
        self._status.update(
            "Saved. Restart to apply loop / quality changes.",
        )
