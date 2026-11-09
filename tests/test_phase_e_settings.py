import json
import os

import pytest

from modules import startup
from ui import web_app
from tests.test_web_6_memory_settings import make_loaded_dashboard


@pytest.fixture
def startup_folder(tmp_path, monkeypatch):
    folder = tmp_path / "Startup"
    folder.mkdir()
    monkeypatch.setattr(startup, "startup_dir", lambda: str(folder))
    return folder


def test_start_with_windows_writes_and_removes_only_its_own_shortcut(startup_folder):
    written = []

    def fake_write(path, target, arguments, folder, icon):
        written.append((path, target, arguments, folder))
        open(path, "w").close()

    (startup_folder / "Rainmeter.lnk").write_text("someone else's")
    assert startup.is_enabled() is False
    ok, message = startup.enable(write=fake_write)
    assert ok and startup.is_enabled()
    path, target, arguments, folder = written[0]
    assert path.endswith("KOVA.lnk") and "--ui" in arguments and arguments.strip('"').startswith(folder)
    assert startup.disable()[0] and not startup.is_enabled()
    assert (startup_folder / "Rainmeter.lnk").exists()   # never touches other shortcuts


def test_start_with_windows_reports_a_missing_folder(monkeypatch):
    monkeypatch.setattr(startup, "startup_dir", lambda: None)
    ok, message = startup.enable(write=lambda *a: None)
    assert not ok and "Startup folder" in message


def test_settings_show_brain_status_without_any_key(startup_folder):
    dashboard, window, controller = make_loaded_dashboard()
    try:
        dashboard.agent.config = {"ASSISTANT_NAME": "Max", "GEMINI_API_KEY": "AIzaSyDecoyDecoyDecoyDecoyDecoyDecoy12"}
        form = dashboard.api.settings()
        assert form["START_WITH_WINDOWS"] is False
        assert form["BRAIN"]["gemini_key"] is True
        assert "Decoy" not in json.dumps(form)
    finally:
        dashboard.destroy()


def test_saving_the_switch_turns_start_with_windows_on_and_off(startup_folder, monkeypatch):
    monkeypatch.setattr(startup, "_write_shortcut", lambda path, *a: open(path, "w").close())
    monkeypatch.setattr(startup.enable, "__defaults__", (startup._write_shortcut,))
    dashboard, window, controller = make_loaded_dashboard()
    try:
        answer = dashboard.api.save({"START_WITH_WINDOWS": True})
        assert "START_WITH_WINDOWS" in answer["changed"] and startup.is_enabled()
        answer = dashboard.api.save({"START_WITH_WINDOWS": False})
        assert "START_WITH_WINDOWS" in answer["changed"] and not startup.is_enabled()
        assert dashboard.api.save({"START_WITH_WINDOWS": False})["changed"] == []
    finally:
        dashboard.destroy()


def test_gemini_key_is_checked_saved_and_never_echoed(monkeypatch):
    switched = []
    monkeypatch.setattr(web_app, "use_new_brain", lambda agent: switched.append(True))
    dashboard, window, controller = make_loaded_dashboard()
    try:
        bad = dashboard.api.set_gemini_key("hello world")
        assert bad["ok"] is False and switched == []
        key = "AIzaSyTestTestTestTestTestTestTestTest1"
        good = dashboard.api.set_gemini_key(key)
        assert good["ok"] and key not in json.dumps(good) and switched == [True]
        assert dashboard.agent.config["GEMINI_API_KEY"] == key
    finally:
        dashboard.destroy()


def test_first_run_shows_once_without_a_key_and_finishing_marks_it_done(monkeypatch):
    dashboard, window, controller = make_loaded_dashboard()
    try:
        dashboard.agent.config = {"ASSISTANT_NAME": "Max"}
        assert dashboard.api.first_run()["show"] is True
        assert dashboard.api.finish_setup("", "")["ok"]
        assert dashboard.agent.config["SETUP_DONE"] is True
        assert dashboard.api.first_run()["show"] is False
    finally:
        dashboard.destroy()


def test_first_run_is_skipped_when_a_key_is_already_there():
    dashboard, window, controller = make_loaded_dashboard()
    try:
        dashboard.agent.config = {"GEMINI_API_KEY": "AIzaSyTestTestTestTestTestTestTestTest1"}
        assert dashboard.api.first_run()["show"] is False
    finally:
        dashboard.destroy()


def test_a_bad_key_in_the_welcome_sheet_keeps_the_sheet_open():
    dashboard, window, controller = make_loaded_dashboard()
    try:
        dashboard.agent.config = {}
        answer = dashboard.api.finish_setup("", "nope")
        assert answer["ok"] is False and not dashboard.agent.config.get("SETUP_DONE")
    finally:
        dashboard.destroy()


def test_the_page_has_a_welcome_sheet_and_a_key_box_that_is_a_password_field():
    html = open("ui/web/index.html", encoding="utf-8").read()
    js = open("ui/web/app.js", encoding="utf-8").read()
    assert 'id="welcome"' in html and 'id="welcome-key"' in html and 'type="password"' in html
    assert 'input.type = "password"' in js and "set_gemini_key" in js and "first_run" in js


def test_a_misheard_voice_fragment_is_shown_but_not_kept(monkeypatch):
    from modules.result import Result
    from ui import history
    dashboard, window, controller = make_loaded_dashboard()
    try:
        dashboard.show_user("camera")
        dashboard.show_result(Result(False, "Sorry, I didn't catch that."), "mic")
        assert history.all_messages() == []
        dashboard.show_user("what time is it")
        dashboard.show_result(Result(True, "It's 5 PM."), "mic")
        assert [m["text"] for m in history.all_messages()] == ["what time is it", "It's 5 PM."]
    finally:
        dashboard.destroy()
