"""Fixes from Bilal's dashboard voice session of 7 Nov evening (16:51-18:12, logs/kova.log)."""
import pytest

from desktop_agent import Agent, fix_misheard
from modules import voice_handler, windows
from modules.result import Result

CONFIG = {"ASSISTANT_NAME": "Max", "WAKE_WORDS": ["max"], "USER_TITLE": "Commander"}


def agent():
    return Agent(dict(CONFIG))


def command_of(max_agent, said):
    return max_agent.match(fix_misheard(max_agent.normalize(said)))[0].name


def test_no_score_from_google_is_unknown_not_half_sure(monkeypatch):
    voice_handler.pick_language({"en-US": ("open chrome", None)}, "en-US")
    assert voice_handler.sure_text() == "?" and not voice_handler.unsure()
    voice_handler.pick_language({"en-US": ("open chrome", 0.2)}, "en-US")
    assert voice_handler.sure_text() == "0.20" and voice_handler.unsure()


@pytest.mark.parametrize("said", ["close the chrome tab", "close this tab", "close the tab", "close current tab"])
def test_close_a_tab_never_closes_the_browser(said):
    assert command_of(agent(), said) == "close tab"


def test_close_the_chrome_tab_presses_ctrl_w_on_chromes_front_window(monkeypatch):
    from modules import system_control
    max_agent = agent()
    monkeypatch.setattr(system_control, "app_windows", lambda procs: [(5, "Docs - Google Chrome")])
    monkeypatch.setattr(windows, "process_of", lambda hwnd: "chrome.exe")
    used = []
    monkeypatch.setattr(max_agent, "_tool", lambda tool_name, **kw: used.append((tool_name, kw)) or Result(True, "ok"))
    max_agent.close_current_tab("chrome")
    assert used == [("close_tab", {"name": "chrome", "title": "Docs - Google Chrome"})]


@pytest.fixture
def three_windows(monkeypatch):
    monkeypatch.setattr(windows, "visible_windows", lambda: [
        (1, "chrome.exe", "YouTube - Google Chrome"), (2, "OpenCode.exe", "OpenCode"), (3, "python.exe", "KOVA — Max")])
    monkeypatch.setattr(windows, "_ours", lambda hwnd: hwnd == 3)


@pytest.mark.parametrize("said", ["your", "your window", "yourself", "kova"])
def test_your_window_is_the_dashboard(three_windows, said):
    assert windows.find(said)[0] == 3


def test_open_code_is_opencode(three_windows):
    assert windows.find("open code")[0] == 2


def test_close_youself_typo_still_exits():
    assert command_of(agent(), "close youself and remember you are bad") == "exit"


@pytest.mark.parametrize("said, arg", [
    ("open notepad and tell me when its visible", "notepad"),
    ("next command just open notepad again and tell me when its on the screen", "notepad"),
    ("try opening task manager then tell me when you see it", "task manager"),
])
def test_open_and_confirm_phrases(said, arg):
    max_agent = agent()
    command, found = max_agent.match(fix_misheard(max_agent.normalize(said)))[:2]
    assert command.name == "open and confirm" and found == arg


def test_open_and_confirm_waits_for_the_real_window(monkeypatch):
    max_agent = agent()
    monkeypatch.setattr(max_agent, "_tool", lambda tool_name, **kw: Result(True, "Opening notepad"))
    monkeypatch.setattr(windows, "find", lambda name: (7, "notepad", "Untitled - Notepad"))
    monkeypatch.setattr(windows, "target_window", lambda: (7, "notepad", "Untitled - Notepad"))
    assert max_agent.open_and_confirm("notepad").message.startswith("Notepad is in front now")
    monkeypatch.setattr(windows, "find", lambda name: (None, None, Result(False, "no")))
    result = max_agent.open_and_confirm("notepad", wait_seconds=0.2)
    assert not result.success and "hasn't shown up" in result.message


def test_no_field_with_no_name_says_so_plainly():
    from modules import ui_automation
    result = ui_automation._one([ui_automation.Control("Play", "button")], "", "ytdesktop", editable=True)
    assert "called  in" not in result.message and "Click the search box" in result.message


def test_file_explorer_is_found_by_its_program(monkeypatch):
    monkeypatch.setattr(windows, "visible_windows", lambda: [(4, "explorer.exe", "Coding Arc"), (5, "chrome.exe", "x")])
    assert windows.find("file explorer")[0] == 4


def test_open_the_d_drive_is_a_folder():
    assert command_of(agent(), "open the d drive") == "open folder"


def test_click_option_in_a_named_window(monkeypatch):
    max_agent = agent()
    used = []
    monkeypatch.setattr(max_agent, "_tool", lambda tool_name, **kw: used.append((tool_name, kw)) or Result(True, "ok"))
    max_agent.raw_text = "click the mobile devices option in setting window"
    max_agent.click_in("mobile devices option in setting window")
    assert used == [("ui_click", {"name": "mobile devices", "window": "setting"})]


def test_say_again_repeats_the_last_answer(monkeypatch):
    max_agent = agent()
    assert max_agent.handle("say again").message == "I haven't said anything yet."
    monkeypatch.setattr(max_agent, "_handle", lambda text: Result(True, "It's 5 PM."))
    max_agent.handle("what's the time")
    assert max_agent.handle("say again").message == "It's 5 PM."
    assert max_agent.handle("dobara bolo").message == "It's 5 PM."


def test_i_see_you_have_is_a_screen_claim():
    from modules import agent_loop
    assert agent_loop.unbacked_claim("I see you have a red mark on the speaker.", set()) == "see"


def test_open_a_named_tab_switches_to_it():
    max_agent = agent()
    command, arg = max_agent.match(max_agent.normalize("open adobe firefly tab"))[:2]
    assert command.name == "switch tab" and arg == "adobe firefly"
    assert command_of(max_agent, "open a new tab") != "switch tab"


def test_open_and_confirm_stays_local_though_it_says_and(monkeypatch):
    from tests.conftest import FakeProvider
    max_agent = Agent(dict(CONFIG), provider=FakeProvider())
    asked = []
    monkeypatch.setattr(max_agent, "_ask_agent", lambda text: asked.append(text) or Result(True, "ai"))
    monkeypatch.setattr(max_agent, "_tool", lambda tool_name, **kw: Result(False, "not opened"))
    assert max_agent.handle("open notepad and tell me when its on the screen").message == "not opened"
    assert asked == []


def test_close_file_explorer_closes_its_window_not_the_process(monkeypatch):
    max_agent = agent()
    assert command_of(max_agent, "close file explorer") == "close explorer"
    monkeypatch.setattr(windows, "find", lambda name: (4, "file explorer", "Coding Arc"))
    closed = []
    monkeypatch.setattr(windows, "close_hwnd", lambda hwnd, app, title: closed.append(hwnd) or Result(True, "ok"))
    assert max_agent.close_explorer().success and closed == [4]


def test_open_a_file_by_name_when_no_app_has_it(monkeypatch):
    from modules import files
    max_agent = agent()
    used = []

    def tool(tool_name, **kw):
        used.append((tool_name, kw))
        return Result(False, "I don't know an app or website called max live check.") if tool_name == "open_app" \
            else Result(True, "Opening max_live_check.txt")
    monkeypatch.setattr(max_agent, "_tool", tool)
    monkeypatch.setattr(files, "find", lambda name, seconds=3: files._found.__setitem__(slice(None), ["C:/d/max_live_check.txt"])
                        or Result(True, "found"))
    assert max_agent.open_any("max live check").success
    assert used[-1] == ("open_file", {"target": "C:/d/max_live_check.txt"})


def test_type_into_the_notepad_window_names_the_window(monkeypatch):
    max_agent = agent()
    used = []
    monkeypatch.setattr(max_agent, "_tool", lambda tool_name, **kw: used.append((tool_name, kw)) or Result(True, "ok"))
    max_agent.raw_text = "type hello from max into the notepad window"
    max_agent.type_into("hello from max into the notepad window")
    assert used == [("ui_type", {"text": "hello from max", "window": "notepad"})]


@pytest.mark.parametrize("said", ["save it", "save", "save the file", "save this now"])
def test_save_it_is_ctrl_s(said):
    max_agent = agent()
    command, _ = max_agent.match(max_agent.normalize(said))[:2]
    assert command.name == "save"
