"""Day 6 step 2: settings — only real changes are written, other config keys are
untouched, and the new values take effect without a restart. The config file is
a temp file (conftest points tools.CONFIG_FILE there); the real one is never opened."""
import json
from pathlib import Path

from desktop_agent import Agent
from modules import alerts, tools, voice_handler
from ui import settings

CONFIG = {"ASSISTANT_NAME": "Max", "WAKE_WORDS": ["max"], "VOICE": "female"}


def agent():
    return Agent(dict(CONFIG), provider=None)


def form(**over):
    """A settings form exactly as the tab fills it in."""
    base = {"VOICE": "female", "ALERTS_ON": False, "WAKE_WORDS": "max", "PTT_HOTKEY": "Ctrl+Alt+M"}
    base.update(over)
    return base


# ---------- reading the form ----------

def test_wake_words_are_cleaned_and_never_duplicated():
    assert settings.parse_wake_words(" Max,  mac ,MAX, ,zed ") == ["max", "mac", "zed"]
    assert settings.parse_wake_words("") == []
    assert settings.parse_wake_words(None) == []


def test_nothing_changed_means_nothing_to_write():
    kova = agent()
    assert settings.changes(kova.config, form()) == {}


def test_only_the_keys_that_really_differ_are_collected():
    kova = agent()
    changed = settings.changes(kova.config, form(VOICE="male", ALERTS_ON=True, WAKE_WORDS="max, zed"))
    assert changed == {"VOICE": "male", "ALERTS_ON": True, "WAKE_WORDS": ["max", "zed"]}


def test_a_config_without_the_keys_uses_the_same_defaults_as_the_form():
    """A fresh config.json has no VOICE/PTT_HOTKEY: saving the defaults is not a change,
    and wake words still showing the assistant's name are not a change either."""
    assert settings.changes({"ASSISTANT_NAME": "Max", "WAKE_WORDS": ["max"]}, form()) == {}
    assert settings.changes({"ASSISTANT_NAME": "Max"}, form(WAKE_WORDS="max")) == {}


def test_an_empty_wake_word_box_keeps_the_old_list():
    kova = agent()
    assert settings.changes(kova.config, form(WAKE_WORDS=" , ")) == {}


# ---------- saving ----------

def test_saving_keeps_every_other_key_including_api_keys():
    path = Path(tools.CONFIG_FILE)
    original = {"GEMINI_API_KEY": "SECRET-KEY-NOT-YOURS", "GEMINI_MODEL": "gemini-3.6-flash",
                "NEWS_API_KEY": "", "ASSISTANT_NAME": "Max", "WAKE_WORDS": ["max"]}
    path.write_text(json.dumps(original), encoding="utf-8")

    settings.save({"VOICE": "male", "WAKE_WORDS": ["max", "zed"]})

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["GEMINI_API_KEY"] == "SECRET-KEY-NOT-YOURS"   # read back to compare, never printed
    assert saved["GEMINI_MODEL"] == original["GEMINI_MODEL"]
    assert saved["NEWS_API_KEY"] == ""
    assert saved["ASSISTANT_NAME"] == "Max"
    assert saved["VOICE"] == "male"
    assert saved["WAKE_WORDS"] == ["max", "zed"]
    # still readable, two-space JSON like every other day
    assert "  \"GEMINI_MODEL\"" in path.read_text(encoding="utf-8")


def test_save_and_apply_writes_and_takes_effect():
    path = Path(tools.CONFIG_FILE)
    path.write_text(json.dumps({"ASSISTANT_NAME": "Max", "WAKE_WORDS": ["max"]}), encoding="utf-8")
    kova = agent()
    hotkeys = []
    changed = settings.save_and_apply(kova, form(VOICE="male", PTT_HOTKEY="ctrl+alt+j"),
                                      on_hotkey=hotkeys.append)

    assert changed == {"VOICE": "male", "PTT_HOTKEY": "ctrl+alt+j"}
    assert kova.config["PTT_HOTKEY"] == "ctrl+alt+j"
    assert voice_handler.current_voice() == "male"
    assert hotkeys == ["ctrl+alt+j"]
    saved = json.loads(Path(tools.CONFIG_FILE).read_text(encoding="utf-8"))
    assert saved["VOICE"] == "male" and saved["PTT_HOTKEY"] == "ctrl+alt+j"
    assert saved["WAKE_WORDS"] == ["max"]  # untouched


def test_saving_nothing_touches_no_file(monkeypatch):
    calls = []
    monkeypatch.setattr(tools, "save_setting", lambda key, value: calls.append(key))
    kova = agent()
    assert settings.save_and_apply(kova, form()) == {}
    assert calls == []


# ---------- taking effect ----------

def test_new_wake_words_work_without_a_restart():
    kova = agent()
    settings.apply(kova, {"WAKE_WORDS": ["zed"]})
    assert kova.wake_words == ["zed"]
    # The command table was rebuilt: the exit command now knows the new wake word
    command, arg = kova.match(kova.normalize("close zed"))
    assert command is not None and command.name == "exit"
    assert kova.find_wake_word("hey zed open notepad") == " open notepad"


def test_alerts_start_and_stop_are_called_never_a_real_watcher(monkeypatch):
    started, stopped = [], []
    monkeypatch.setattr(alerts, "start", lambda config: started.append(config))
    monkeypatch.setattr(alerts, "stop", lambda: stopped.append(True))

    kova = agent()
    settings.apply(kova, {"ALERTS_ON": True})
    settings.apply(kova, {"ALERTS_ON": False})
    assert started == [kova.config]
    assert stopped == [True]


def test_voice_setting_is_applied_live():
    kova = agent()
    settings.apply(kova, {"VOICE": "male"})
    assert voice_handler.current_voice() == "male"
