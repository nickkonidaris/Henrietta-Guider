"""Alert banner widget with audio dispatch.

A textual Widget that surfaces severity-tagged messages from the
controller. The banner is hidden by default and reveals itself
(via the .warn / .alert / .error classes) when ``show()`` is
called. Audio + speech are dispatched lazily so the widget module
can be imported in headless / SSH contexts without pulling in
``henrietta_guider.core.audio`` until an alert actually fires.
"""

from __future__ import annotations

from textual.widget import Widget
from textual.widgets import Static


class AlertBanner(Widget):
    DEFAULT_CSS = """
    AlertBanner { display: none; height: 3; padding: 1; }
    AlertBanner.warn  { background: #F2A65A; display: block; }
    AlertBanner.alert { background: #F26D5B; display: block; }
    AlertBanner.error { background: #E63946; display: block; }
    """

    def __init__(
        self,
        *,
        audio_alerts: bool,
        audio_speak: bool,
        audio_sound_path: str | None,
    ) -> None:
        super().__init__()
        self.audio_alerts = audio_alerts
        self.audio_speak = audio_speak
        self.audio_sound_path = audio_sound_path
        self._label = Static("")

    def compose(self):
        yield self._label

    def show(
        self,
        severity: str,
        message: str,
        spoken: str | None = None,
        sound: bool = True,
    ):
        self.set_classes({severity})
        self._label.update(message)
        if sound and self.audio_sound_path:
            from henrietta_guider.core import audio as core_audio

            core_audio.play_sound(self.audio_sound_path, enabled=self.audio_alerts)
        if spoken:
            from henrietta_guider.core import audio as core_audio

            core_audio.speak(spoken, enabled=self.audio_speak)

    def hide(self):
        self.set_classes(set())
