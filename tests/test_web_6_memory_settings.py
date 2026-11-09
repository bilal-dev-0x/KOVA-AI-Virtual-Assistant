"""The new dashboard, step 6: Memory and Settings. Memory reading goes straight
back to the page; forgetting runs the worker's safe delete and pushes the fresh
list; Settings reads and writes only its four keys and never carries a config
secret. The config file is a temp file via conftest, exactly like test_day6.
"""
import json
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from modules.result import Result
from modules import tools
from ui import web_app

from tests.test_web_1_skeleton import FakeAgent, FakeWebViewWindow, js_values
from tests.test_web_4_activity import make_dashboard as make_loaded_dashboard


def wait_done(predicate, seconds=2.0):
    end = time.time() + seconds
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def real_dashboard(kova):
    """A WebDashboard over a real Agent, no windows, no sampler."""
    from ui.bridge import Bridge
    from ui.controller import Controller

    controller = Controller(kova, Bridge())
    dashboard = web_app.WebDashboard(
        controller, kova, create_window=lambda **kw: FakeWebViewWindow(),
        stats_factory=lambda push: SimpleNamespace(start=lambda: None, stop=lambda: None))
    dashboard._pump_stop.set()
    return dashboard


# ---------- memory ----------

def test_memory_api_returns_id_and_text_rows(monkeypatch):
    dashboard, window, controller = make_loaded_dashboard()
    try:
        monkeypatch.setattr("ui.memory.rows",
                            lambda: [(3, "I like lo-fi when coding"), (7, "deadline Friday")])
        assert dashboard.api.memory() == [[3, "I like lo-fi when coding"], [7, "deadline Friday"]]
    finally:
        dashboard.destroy()


def test_forget_runs_the_safe_delete_and_pushes_the_fresh_list(monkeypatch):
    dashboard, window, controller = make_loaded_dashboard()
    try:
        window.events.fire("loaded")   # now events may leave for the page
        dashboard._flush_pending()
        deletions = []
        monkeypatch.setattr("ui.memory.delete",
                            lambda agent, fact_id: deletions.append((agent, fact_id))
                            or Result(True, "Forgot memory number 3."))
        monkeypatch.setattr("ui.memory.rows", lambda: [(7, "deadline Friday")])

        assert dashboard.controller.run(dashboard.make_forget_job(3)) is True
        assert wait_done(lambda: not controller.busy)   # the worker settles
        for _ in range(3):                              # the pump flushes what the worker posted
            controller.bridge.next_call(timeout=0)

        payload = [p for p in js_values(window) if p["t"] == "memory"]
        assert payload and payload[-1]["rows"] == [[7, "deadline Friday"]]
        assert deletions == [(controller.agent, 3)]    # the same safe delete path as the old tab

        chat = [p for p in js_values(window) if p["t"] == "chat" and p["who"] == "max"]
        assert "Forgot memory number 3." in chat[-1]["text"]   # the tool's own answer, typed and spoken
    finally:
        controller.stop()
        dashboard.destroy()


def test_the_fresh_list_follows_even_a_failed_delete(monkeypatch):
    dashboard, window, controller = make_loaded_dashboard()
    try:
        window.events.fire("loaded")
        dashboard._flush_pending()
        monkeypatch.setattr("ui.memory.delete", lambda agent, fact_id: (_ for _ in ()).throw(ValueError("no runner")))
        monkeypatch.setattr("ui.memory.rows", lambda: [(3, "kept")])

        assert dashboard.controller.run(dashboard.make_forget_job(3)) is True
        assert wait_done(lambda: not controller.busy)
        for _ in range(3):
            controller.bridge.next_call(timeout=0)
        payload = [p for p in js_values(window) if p["t"] == "memory"]
        assert payload and payload[-1]["rows"] == [[3, "kept"]]
    finally:
        controller.stop()
        dashboard.destroy()


# ---------- settings ----------

def test_settings_api_reads_only_its_four_keys():
    dashboard, window, controller = make_loaded_dashboard()
    try:
        dashboard.agent.config = {
            "ASSISTANT_NAME": "Max", "USER_TITLE": "Commander", "WAKE_WORDS": ["max", "mac"],
            "VOICE": "female", "ALERTS_ON": True, "PTT_HOTKEY": "F9",
            "GEMINI_API_KEY": "decoy-not-mine", "BRAIN_PROVIDER": "gemini",
        }
        form = dashboard.api.settings()
        assert form.pop("START_WITH_WINDOWS") is False and form.pop("BRAIN")["gemini_key"] is True
        assert "decoy" not in str(form)
        assert form.pop("PERSONAS") == [{"id": "max", "name": "Max"}, {"id": "mira", "name": "Mira"}]
        assert form == {"PERSONA": "max", "VOICE": "female", "ALERTS_ON": True, "PET_ON": False,
                        "WAKE_WORDS": "max, mac", "PTT_HOTKEY": "F9"}
        assert "GEMINI_API_KEY" not in form and "decoy" not in str(form)
    finally:
        dashboard.destroy()


def test_the_config_defaults_fill_the_form_when_keys_are_missing():
    dashboard, window, controller = make_loaded_dashboard()
    try:
        dashboard.agent.config = {"ASSISTANT_NAME": "Max"}   # a fresh config: no VOICE/PTT keys
        form = dashboard.api.settings()
        form.pop("PERSONAS"), form.pop("START_WITH_WINDOWS"), form.pop("BRAIN")
        assert form == {"PERSONA": "max", "VOICE": "female", "ALERTS_ON": False, "PET_ON": False,
                        "WAKE_WORDS": "max", "PTT_HOTKEY": "Ctrl+Alt+M"}
    finally:
        dashboard.destroy()


def test_save_goes_through_the_settings_module_and_reports_a_refused_hotkey(monkeypatch):
    dashboard, window, controller = make_loaded_dashboard()
    try:
        seen = {}

        def fake_save_and_apply(agent, form, on_hotkey=None):
            seen["agent"], seen["form"] = agent, form
            on_hotkey("Ctrl+Shift+K")
            return {"VOICE": "male", "PTT_HOTKEY": "Ctrl+Shift+K"}

        monkeypatch.setattr("ui.settings.save_and_apply", fake_save_and_apply)

        dashboard.on_hotkey = lambda spec: True   # a normal run: hotkey.start succeeds
        answered = dashboard.api.save({"VOICE": "male"})
        assert seen["agent"] is dashboard.agent
        assert answered == {"changed": ["VOICE", "PTT_HOTKEY"], "refused": [], "notes": []}

        dashboard.on_hotkey = lambda spec: False   # this run could not take the new key
        answered = dashboard.api.save({"VOICE": "male"})
        assert answered["refused"] == ["Ctrl+Shift+K"]   # reported, so the page can say so
    finally:
        dashboard.destroy()


def test_save_really_writes_only_the_changed_keys():
    """One real save on the temp config file: one key changes, the API keys
    stay byte-identical, and nothing else of the config is touched."""
    kova_file = Path(tools.CONFIG_FILE)
    kova_file.write_text(json.dumps({
        "ASSISTANT_NAME": "Max", "WAKE_WORDS": ["max"], "VOICE": "female",
        "GEMINI_API_KEY": "decoy-not-mine"}), encoding="utf-8")
    from desktop_agent import Agent

    kova = Agent({"ASSISTANT_NAME": "Max", "WAKE_WORDS": ["max"], "VOICE": "female"}, provider=None)
    dashboard = real_dashboard(kova)
    try:
        answered = dashboard.api.save({"VOICE": "male", "ALERTS_ON": None,
                                       "WAKE_WORDS": "max", "PTT_HOTKEY": "Ctrl+Alt+M"})
        assert answered["changed"] == ["VOICE"] and answered["refused"] == []
        saved = json.loads(kova_file.read_text(encoding="utf-8"))
        assert saved["VOICE"] == "male" and saved["GEMINI_API_KEY"] == "decoy-not-mine"
        assert "ALERTS_ON" not in saved and "PTT_HOTKEY" not in saved
    finally:
        dashboard.destroy()


def test_a_hotkey_refusal_still_reports_and_the_live_config_takes_the_value():
    from desktop_agent import Agent

    hotkeys = []
    kova = Agent({"ASSISTANT_NAME": "Max", "WAKE_WORDS": ["max"]}, provider=None)
    dashboard = real_dashboard(kova)
    try:
        # for the hotkey to change at all, the form must differ from the default
        dashboard.on_hotkey = lambda spec: hotkeys.append(spec) or False
        answered = dashboard.api.save({"VOICE": "female", "ALERTS_ON": None,
                                       "WAKE_WORDS": "max", "PTT_HOTKEY": "ctrl+alt+j"})
        assert answered["changed"] == ["PTT_HOTKEY"]
        assert answered["refused"] == ["ctrl+alt+j"]
        assert kova.config["PTT_HOTKEY"] == "ctrl+alt+j"   # saved and live...
        assert dashboard.on_hotkey("x") is False           # ...yet registration keeps refusing
    finally:
        dashboard.destroy()


# ---------- the page side ----------

def test_the_memory_and_settings_pages_are_wired_on_the_page_side():
    web = Path(__file__).resolve().parents[1] / "ui" / "web"
    html = (web / "index.html").read_text(encoding="utf-8")
    js = (web / "app.js").read_text(encoding="utf-8")

    for element in ("memory-list", "memory-refresh", "settings-form", "settings-status"):
        assert f'id="{element}"' in html, element
    assert "api.memory()" in js and "api.forget(" in js      # read straight, forget through the worker
    assert "api.settings()" in js and "api.save(" in js
    assert not re.search(r"\.innerHTML\s*=|insertAdjacentHTML", js)   # user text never becomes markup
    for control in ("set-alerts", "set-wake-words", "set-hotkey"):
        assert control in js
