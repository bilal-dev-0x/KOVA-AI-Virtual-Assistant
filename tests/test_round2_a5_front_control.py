from types import SimpleNamespace

from desktop_agent import Agent
from modules import system_control, window_watch
from modules.result import Result


def test_open_downloads_clicks_the_unique_front_control(monkeypatch):
    agent = Agent({"ASSISTANT_NAME": "Max"}, provider=None)
    monkeypatch.setattr(system_control, "resolve", lambda name: None)
    monkeypatch.setattr("modules.windows.target_window", lambda: (7, "Epic", "Launcher"))
    monkeypatch.setattr(window_watch, "controls_for", lambda hwnd, title: [SimpleNamespace(name="Downloads")])
    calls = []
    monkeypatch.setattr(agent, "_tool", lambda tool_name, **args: calls.append((tool_name, args)) or Result(True, "Clicked Downloads."))
    assert agent.handle("open the download").success
    assert calls == [("ui_click", {"name": "Downloads"})]


def test_open_notepad_stays_an_app_open(monkeypatch):
    agent = Agent({}, provider=None)
    monkeypatch.setattr(system_control, "resolve", lambda name: ("app", "notepad", {}))
    calls = []
    monkeypatch.setattr(agent, "_tool", lambda tool_name, **args: calls.append((tool_name, args)) or Result(True, "Opened Notepad."))
    assert agent.handle("open notepad").success
    assert calls == [("open_app", {"name": "notepad"})]


def test_open_control_ambiguity_names_controls(monkeypatch):
    agent = Agent({}, provider=None)
    monkeypatch.setattr(system_control, "resolve", lambda name: None)
    monkeypatch.setattr("modules.windows.target_window", lambda: (7, "Epic", "Launcher"))
    monkeypatch.setattr(window_watch, "controls_for", lambda hwnd, title: [SimpleNamespace(name="Downloads"), SimpleNamespace(name="Downloads")])
    result = agent.open_any("downloads")
    assert "more than one downloads" in result.message
    assert "Downloads" in result.message
