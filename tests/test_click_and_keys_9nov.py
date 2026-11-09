import time

import pytest

from modules import system_control, ui_automation as ui, window_watch, windows
from modules.ui_automation import Control
from tests.test_day4_ui import app  # noqa: F401  (the fake window in front)


def test_a_button_deep_in_a_web_page_is_found_by_name(app, monkeypatch):
    app.controls = [Control("Back", "button"), Control("Address and search bar", "field")]
    deep = Control("Run speed test", "button")
    monkeypatch.setattr(ui, "_find_named", lambda hwnd, name, exact=False: [deep])
    result = ui.click("run speed test")
    assert result.success and app.did == [("click", "Run speed test")]


def test_the_deep_search_is_not_used_when_the_quick_read_already_found_it(app, monkeypatch):
    app.controls = [Control("Save", "button")]
    monkeypatch.setattr(ui, "_find_named", lambda *a, **k: pytest.fail("searched again"))
    assert ui.click("save").success


def test_names_read_ahead_make_the_click_skip_the_read(app, monkeypatch):
    monkeypatch.undo()   # use the real _from_seen, with the fake window kept below
    from tests.test_day4_ui import FakeApp
    monkeypatch.setattr(windows, "visible_windows", lambda: [(7, "notepad.exe", "Untitled - Notepad")])
    monkeypatch.setattr(windows, "_is_minimised", lambda h: False)
    monkeypatch.setattr(windows, "_active_popup", lambda h: None)
    monkeypatch.setattr(windows, "_runs_as_admin", lambda h: False)
    monkeypatch.setattr(system_control, "_window_title", lambda h: "Untitled - Notepad")
    monkeypatch.setattr(ui, "_is_hung", lambda h: False)
    monkeypatch.setattr(ui, "_is_terminal", lambda h: False)
    clicked = []
    monkeypatch.setattr(ui, "_invoke", lambda c: clicked.append(c.name) or True)
    monkeypatch.setattr(ui, "_read_tree", lambda *a: pytest.fail("read the whole window again"))
    live = Control("Save", "button", element="live")
    monkeypatch.setattr(ui, "_find_named", lambda hwnd, name, exact=False: [live] if exact and name == "Save" else [])
    window_watch.forget_all()
    window_watch.remember(7, "Untitled - Notepad", [Control("Save", "button"), Control("Open", "button")])
    try:
        assert ui.click("save").success and clicked == ["Save"]
    finally:
        window_watch.forget_all()


def test_read_ahead_keeps_names_only_and_forgets_old_or_retitled_windows(monkeypatch):
    window_watch.forget_all()
    window_watch.remember(5, "Login", [Control("Sign in", "button", element=object()),
                                       Control("hunter2", "field", password=True)])
    seen = window_watch.controls_for(5, "Login")
    assert [c.name for c in seen] == ["Sign in"] and seen[0].element is None
    assert window_watch.controls_for(5, "Other title") is None
    later = time.time() + window_watch.FRESH_SECONDS + 5
    monkeypatch.setattr(window_watch.time, "time", lambda: later)
    assert window_watch.controls_for(5, "Login") is None
    window_watch.forget_all()


def test_read_ahead_can_be_turned_off():
    assert window_watch.start({"WINDOW_WATCH": False}) is False


def test_click_in_a_browser_without_saying_window_or_with_windows():
    from desktop_agent import Agent
    agent = Agent({"ASSISTANT_NAME": "Max"}, provider=None)
    asked = []
    agent._tool = lambda tool, **args: asked.append((tool, args))
    agent.click_in("run speed test in chrome windows")
    agent.click_in("run speed test in chrome")
    agent.click_in("sign in")
    assert asked[0] == ("ui_click", {"name": "run speed test", "window": "chrome"})
    assert asked[1] == ("ui_click", {"name": "run speed test", "window": "chrome"})
    assert asked[2][1] == {"name": "sign in"}


def test_enter_approves_only_a_confirm_sheet_and_not_in_its_first_half_second():
    js = open("ui/web/app.js", encoding="utf-8").read()
    assert 'keyEvent.key === "Enter" && dialog.token !== null && dialog.kind === "confirm"' in js
    assert "dialog.openedAt" in js and "> 500" in js
    assert '"focus-input": applyFocusInput' in js


def test_the_type_now_hotkey_brings_the_box_and_approvals_come_to_the_front(monkeypatch):
    from tests.test_web_6_memory_settings import make_loaded_dashboard
    from tests.test_web_1_skeleton import js_values
    dashboard, window, controller = make_loaded_dashboard()
    try:
        fronts = []
        monkeypatch.setattr(type(dashboard), "_to_front", lambda self: fronts.append(True))
        dashboard.focus_input()
        dashboard._flush_pending()
        assert {"t": "focus-input"} in js_values(window) and fronts
    finally:
        dashboard.destroy()


def test_run_ui_takes_the_type_now_hotkey():
    from ui.hotkey import TYPE_HOTKEY
    assert TYPE_HOTKEY == "Ctrl+Alt+K"
    from tests.test_day6_run import FakeAgent, FakeDashboard, FakeHotkey, FakeTray
    from ui.run import run_ui
    keys = []

    def factory(callback):
        key = FakeHotkey(callback)
        keys.append(key)
        return key

    run_ui(FakeAgent(), dashboard_factory=FakeDashboard, tray_factory=FakeTray, hotkey_factory=factory,
           listen=lambda: "mic phrase", speak=lambda text: None)
    assert keys[1].started == ["Ctrl+Alt+K"] and keys[1].stopped
