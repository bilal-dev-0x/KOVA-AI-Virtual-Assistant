import json

import pytest

from desktop_agent import Agent
from modules import tools, voice_handler


@pytest.mark.parametrize("text, gender", [
    ("use a male voice", "male"),
    ("switch to female voice", "female"),
    ("change your voice to a man", "male"),
    ("talk like a girl", "female"),
    ("can you speak in a male voice", "male"),
])
def test_voice_commands(text, gender):
    result = Agent().handle(text)
    assert result.message == f"Okay, I'll use the {gender} voice now."
    assert voice_handler.current_voice() == gender


def test_change_your_voice_switches_to_the_other_one():
    voice_handler.set_voice("female")
    Agent().handle("change your voice")
    assert voice_handler.current_voice() == "male"


def test_voice_is_saved_without_touching_other_settings(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"GEMINI_API_KEY": "secret", "VOICE": "female"}))
    Agent().handle("use a male voice")
    saved = json.loads((tmp_path / "config.json").read_text())
    assert saved == {"GEMINI_API_KEY": "secret", "VOICE": "male"}


def test_undo_goes_back_to_the_old_voice():
    voice_handler.set_voice("female")
    agent = Agent()
    agent.handle("use a male voice")
    assert agent.handle("undo").message == "Undone. Back to the female voice."
    assert voice_handler.current_voice() == "female"


def test_voice_from_config():
    voice_handler.configure({"VOICE": "male"})
    assert voice_handler.current_voice() == "male"


def test_male_voice_never_falls_back_to_the_female_google_voice(monkeypatch):
    # When the natural voice service is down, a male voice must not suddenly sound female
    monkeypatch.setattr(voice_handler, "_edge_mp3", lambda text, filename: False)
    spoken = []
    monkeypatch.setattr(voice_handler, "_speak_offline", lambda text: spoken.append(voice_handler.current_voice()))
    voice_handler.set_voice("male")
    voice_handler.speak("hello")
    assert spoken == ["male"]


def test_ai_can_switch_the_voice():
    assert tools.set_voice(gender="female").success
    assert voice_handler.current_voice() == "female"


def test_backup_voice_uses_a_fresh_engine_every_time(monkeypatch):
    # A reused pyttsx3 engine went silent from the second line on (relay voice test)
    import pyttsx3
    engines = []

    class Engine:
        def setProperty(self, *args): pass
        def getProperty(self, name): return []
        def say(self, text): self.text = text
        def runAndWait(self): pass
        def stop(self): pass

    monkeypatch.setattr(pyttsx3, "init", lambda: engines.append(Engine()) or engines[-1])
    voice_handler._speak_offline("one")
    voice_handler._speak_offline("two")
    assert [e.text for e in engines] == ["one", "two"]
