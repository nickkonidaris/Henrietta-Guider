import sys

import pytest

from henrietta_guider.core import audio


@pytest.mark.unit
class TestAudio:
    def test_play_sound_uses_afplay_on_macos(self, monkeypatch):
        calls: list[list[str]] = []
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(audio, "_spawn", lambda argv: calls.append(list(argv)) or None)
        ok = audio.play_sound("/System/Library/Sounds/Submarine.aiff")
        assert ok is True
        assert calls == [["afplay", "/System/Library/Sounds/Submarine.aiff"]]

    def test_play_sound_uses_paplay_on_linux(self, monkeypatch):
        calls = []
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(audio, "_spawn", lambda argv: calls.append(list(argv)) or None)
        audio.play_sound("/usr/share/sounds/freedesktop/stereo/bell.oga")
        assert calls[0][0] in {"paplay", "aplay"}

    def test_speak_uses_say_on_macos(self, monkeypatch):
        calls = []
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(audio, "_spawn", lambda argv: calls.append(list(argv)) or None)
        audio.speak("target change possible")
        assert calls == [["say", "target change possible"]]

    def test_speak_uses_espeak_on_linux(self, monkeypatch):
        calls = []
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(audio, "_spawn", lambda argv: calls.append(list(argv)) or None)
        audio.speak("hello world")
        assert calls[0][0] == "espeak"

    def test_play_sound_disabled_returns_false_no_call(self, monkeypatch):
        calls = []
        monkeypatch.setattr(audio, "_spawn", lambda argv: calls.append(list(argv)) or None)
        ok = audio.play_sound("/x/y.aiff", enabled=False)
        assert ok is False
        assert calls == []
