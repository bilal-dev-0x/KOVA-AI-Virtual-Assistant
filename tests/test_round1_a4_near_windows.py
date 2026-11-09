from modules import windows
from modules.result import Result
from desktop_agent import Agent


def _install(monkeypatch, rows):
    monkeypatch.setattr(windows.system_control, "_resolve_app", lambda name: (None, {}))
    monkeypatch.setattr(windows.system_control, "app_windows", lambda processes: [])
    monkeypatch.setattr(windows, "visible_windows", lambda: rows)
    monkeypatch.setattr(windows, "_ours", lambda hwnd: False)


def test_misheard_crow_resolves_to_the_only_open_chrome(monkeypatch):
    _install(monkeypatch, [(1, "chrome.exe", "Google Chrome")])

    hwnd, app, title = windows.find("crow")

    assert (hwnd, app, title) == (1, "chrome", "Google Chrome")


def test_misheard_name_asks_when_two_windows_match(monkeypatch):
    _install(monkeypatch, [(1, "chrome.exe", "YouTube - Google Chrome"),
                           (2, "chrome.exe", "Gmail - Google Chrome")])

    hwnd, app, problem = windows.find("crow")

    assert hwnd is None and app is None
    assert isinstance(problem, Result) and problem.halt
    assert len(problem.choices) == 2
    assert "YouTube" in problem.message and "Gmail" in problem.message


def test_unrelated_misheard_name_does_not_pick_a_window(monkeypatch):
    _install(monkeypatch, [(1, "chrome.exe", "Google Chrome")])

    hwnd, app, problem = windows.find("calculator")

    assert hwnd is None and app is None
    assert not problem.success
    assert "can't see" in problem.message


def test_move_reports_the_word_it_matched(monkeypatch):
    _install(monkeypatch, [(1, "chrome.exe", "Google Chrome")])
    monkeypatch.setattr(windows, "_runs_as_admin", lambda hwnd: False)
    monkeypatch.setattr(windows, "_save", lambda hwnd: (hwnd, "chrome", "Google Chrome"))
    monkeypatch.setattr(windows, "_show", lambda hwnd, command: True)
    monkeypatch.setattr(windows, "_put_back", lambda saved: lambda: None)

    result = windows.move("crow", "minimise")

    assert result.success
    assert result.message == "Minimised Chrome (I heard crow)."


def test_close_uses_one_fuzzy_matched_window_not_the_whole_app(monkeypatch):
    monkeypatch.setattr(windows.system_control, "_resolve_app",
                        lambda name: ("chrome", {"aliases": ["google chrome"], "process": ["chrome.exe"]}))
    monkeypatch.setattr(windows, "find", lambda name: (11, "chrome", "Gmail - Google Chrome"))
    monkeypatch.setattr(windows, "process_of", lambda hwnd: "chrome.exe")

    agent = Agent({"AGENT_TRACE": False}, provider=None)
    key, problem = agent._prepare_close("crome")

    assert key == "chrome" and problem is None
    assert agent.closing_title == ("chrome", "Gmail - Google Chrome")
    assert agent.closing_window is None


def test_close_keeps_ambiguous_fuzzy_windows_unclosed(monkeypatch):
    ambiguous = Result(False, "Which window did you mean: Chrome — Gmail; Chrome — YouTube?",
                       halt=True, choices=("Chrome — Gmail", "Chrome — YouTube"))
    monkeypatch.setattr(windows.system_control, "_resolve_app",
                        lambda name: ("chrome", {"aliases": ["google chrome"], "process": ["chrome.exe"]}))
    monkeypatch.setattr(windows, "find", lambda name: (None, None, ambiguous))

    agent = Agent({"AGENT_TRACE": False}, provider=None)
    key, problem = agent._prepare_close("crome")

    assert key is None and problem is ambiguous
    assert agent.closing_title is None and agent.closing_window is None
