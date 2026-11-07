"""Fixes from the dashboard live test of 7 Nov (python main.py --ui, commands typed into the window)."""
import os
from pathlib import Path

import pytest

from desktop_agent import Agent, fix_misheard
from modules import files, windows
from modules.result import Result

CONFIG = {"ASSISTANT_NAME": "Max", "WAKE_WORDS": ["max"], "USER_TITLE": "Commander"}


def agent():
    return Agent(dict(CONFIG))


@pytest.mark.parametrize("said, command", [
    ("how are you doing today", "greet"),
    ("hey how are you today", "greet"),
    ("open youtube in a new tab", "open in tab"),
    ("open github.com in a new tab", "open in tab"),
    ("open the folder old notes", "open folder"),
    ("open the file budget plan final", "open file"),
])
def test_phrases_from_the_dashboard_test(said, command):
    max_agent = agent()
    assert max_agent.match(fix_misheard(max_agent.normalize(said)))[0].name == command


def test_open_in_a_new_tab_opens_the_site_not_an_app_called_that():
    max_agent = agent()
    assert max_agent.match(max_agent.normalize("open youtube in a new tab"))[1] == "youtube"


def _deep_file(name="budget_plan_final.txt"):
    folder = Path(files.known_folder("documents")) / "projects" / "2024" / "archive" / "old_notes"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("test")
    return path


def test_open_file_finds_a_deep_file_without_find_first(monkeypatch):
    path = _deep_file()
    files._found.clear()
    started = []
    monkeypatch.setattr(files, "_start", started.append)
    result = files.open_item("budget plan final")
    assert result.success and started == [str(path)]


def test_open_file_with_two_hits_lists_them(monkeypatch):
    _deep_file("report_q1.txt")
    other = Path(files.known_folder("downloads")) / "work" / "report_q1.docx"
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text("x")
    started = []
    monkeypatch.setattr(files, "_start", started.append)
    result = files.open_item("report q1")
    assert started == [] and "Say open the first one" in result.message


def test_open_the_folder_old_notes_finds_it_by_name():
    path = _deep_file().parent
    assert files.find_folder("old notes") == str(path)


def test_the_dashboard_is_never_the_window_max_works_in(monkeypatch):
    # The dashboard is in front while you type; "save this file" means the window behind it
    monkeypatch.setattr(windows, "visible_windows",
                        lambda: [(1, "python.exe", "KOVA — Max"), (2, "notepad.exe", "notes - Notepad")])
    monkeypatch.setattr(windows, "_ours", lambda hwnd: hwnd == 1)
    monkeypatch.setattr(windows, "_is_minimised", lambda hwnd: False)
    monkeypatch.setattr(windows, "_active_popup", lambda hwnd: None)
    assert windows.target_window() == (2, windows.app_name("notepad.exe"), "notes - Notepad")


def test_close_all_youtube_tabs_is_a_local_command():
    max_agent = agent()
    command, arg = max_agent.match(max_agent.normalize("close all youtube tabs"))[:2]
    assert command.name == "close tabs" and arg == "youtube"


def test_close_tabs_closes_each_matching_tab(monkeypatch):
    from modules import system_control
    tabs = ["lofi - YouTube - Google Chrome", "song - YouTube - Google Chrome", "GitHub - Google Chrome"]
    monkeypatch.setattr(system_control, "app_windows", lambda processes: [(1, tabs[0])])
    monkeypatch.setattr(system_control, "_bring_to_front", lambda hwnd: True)
    monkeypatch.setattr(system_control, "_window_title", lambda hwnd: tabs[0])

    def switch(query):
        return Result(True, "shown") if "YouTube" in tabs[0] else Result(False, "none")
    monkeypatch.setattr(system_control, "switch_tab", switch)
    monkeypatch.setattr(system_control, "_press_ctrl_w", lambda: tabs.pop(0))
    result = system_control.close_tabs("youtube")
    assert result.success and result.message == "Closed 2 youtube tabs." and tabs == ["GitHub - Google Chrome"]


def test_save_as_field_is_the_file_name(monkeypatch):
    from modules import ui_automation
    fields = [ui_automation.Control(n, "field") for n in ("File name:", "Name", "Name", "Search Box")]
    assert ui_automation._one(fields, "text", "notepad", editable=True).name == "File name:"


def test_close_tabs_with_two_identical_titles_counts_tabs(monkeypatch):
    from modules import system_control
    tabs = ["(31) YouTube - Google Chrome", "(31) YouTube - Google Chrome", "GitHub - Google Chrome"]
    monkeypatch.setattr(system_control, "app_windows", lambda processes: [(1, tabs[0])])
    monkeypatch.setattr(system_control, "_bring_to_front", lambda hwnd: True)
    monkeypatch.setattr(system_control, "_window_title", lambda hwnd: tabs[0])
    monkeypatch.setattr(system_control, "_tab_count", lambda hwnd: len(tabs))
    monkeypatch.setattr(system_control, "switch_tab",
                        lambda query: Result("YouTube" in tabs[0], "shown"))
    monkeypatch.setattr(system_control, "_press_ctrl_w", lambda: tabs.pop(0))
    result = system_control.close_tabs("youtube", wait_seconds=0.1)
    assert result.message == "Closed 2 youtube tabs." and len(tabs) == 1


def test_gemini_busy_after_a_tool_hands_the_request_to_the_server():
    from brain.base import BrainError, Reply
    from brain.hybrid import HybridProvider
    from tests.conftest import FakeProvider

    class BusyAfterTool(FakeProvider):
        def session(self, system_prompt, tools):
            session = super().session(system_prompt, tools)

            def busy(results):
                raise BrainError("Gemini is busy right now.")
            session.send_results = busy
            return session

    gemini = BusyAfterTool([Reply(text="", calls=[])])
    server = FakeProvider([Reply(text="Here is a space fact.")])
    hybrid = HybridProvider(gemini, server)
    session = hybrid.session("prompt", [])
    session.send("tell me a fact about space")
    call = type("Call", (), {"name": "web_search"})()
    reply = session.send_results([(call, {"ok": False, "message": "search failed"})])
    assert reply.text == "Here is a space fact."
    assert "already ran" in server.fake_session.sent[-1] and "web_search" in server.fake_session.sent[-1]


def test_d_drive_folder_with_the_word_folder(monkeypatch):
    from modules import system_control
    monkeypatch.setattr(system_control.os.path, "isdir", lambda p: p in ("D:\\", "D:\\Coding Arc"))
    monkeypatch.setattr(system_control.os, "listdir", lambda p: ["Coding Arc", "Games"] if p == "D:\\" else [])
    assert system_control.spoken_path("d drive coding arc folder") == "D:\\Coding Arc"
