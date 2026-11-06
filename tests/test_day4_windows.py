"""Day 4 step 1: window control. A fake screen stands in for Windows: no real window moves."""
import pytest

from brain.base import Reply
from desktop_agent import Agent, side_by_side_args
from modules import system_control, windows
from tests.conftest import FakeProvider

CONFIG = {"ASSISTANT_NAME": "Max", "WAKE_WORDS": ["max"]}
WORK = (0, 0, 1920, 1040)        # 1920x1080 with the taskbar
SECOND = (1920, 0, 3840, 1040)   # a second monitor to the right


class FakeScreen:
    def __init__(self):
        self.win = {}
        self.calls = []

    def add(self, hwnd, process, title, work=WORK, state="normal", admin=False, real=None, blocked=False):
        # pos: the floating rectangle Windows keeps in the placement; real: where the window
        # really is when Windows snapped it itself (Win+Left); blocked: every move fails
        self.win[hwnd] = {"process": process, "title": title, "work": work, "state": state, "admin": admin,
                          "pos": (100, 100, 800, 600), "real": real, "blocked": blocked, "closed": False}

    # stand-ins for the Win32 calls
    def visible(self):
        return [(h, w["process"], w["title"]) for h, w in self.win.items()]

    def app_windows(self, procs):
        wanted = {p.lower() for p in procs}
        return [(h, w["title"]) for h, w in self.win.items() if w["process"] in wanted]

    def get_placement(self, hwnd):
        return (self.win[hwnd]["state"], self.win[hwnd]["pos"])

    def set_placement(self, hwnd, placement):
        self.calls.append(("placement", hwnd))
        if self.win[hwnd]["closed"]:
            return False
        self.win[hwnd]["state"], self.win[hwnd]["pos"] = placement
        self.win[hwnd]["real"] = None
        return True

    def get_rect(self, hwnd):
        return self.win[hwnd]["real"] or self.win[hwnd]["pos"]

    @staticmethod
    def normal_rect(placement):
        state, pos = placement
        return pos if state == "normal" else None

    def show(self, hwnd, command):
        self.calls.append(("show", hwnd, command))
        self.win[hwnd]["state"] = {windows.SW_MAXIMIZE: "max", windows.SW_MINIMIZE: "min",
                                   windows.SW_RESTORE: "normal"}[command]
        return True

    def set_pos(self, hwnd, rect):
        self.calls.append(("pos", hwnd, rect, self.win[hwnd]["state"]))
        if self.win[hwnd]["blocked"]:
            return False
        self.win[hwnd]["pos"], self.win[hwnd]["real"] = rect, None
        return True


@pytest.fixture
def screen(monkeypatch):
    fake = FakeScreen()
    monkeypatch.setattr(windows, "visible_windows", fake.visible)
    monkeypatch.setattr(system_control, "app_windows", fake.app_windows)
    monkeypatch.setattr(windows, "_get_placement", fake.get_placement)
    monkeypatch.setattr(windows, "_set_placement", fake.set_placement)
    monkeypatch.setattr(windows, "_get_rect", fake.get_rect)
    monkeypatch.setattr(windows, "_normal_rect", fake.normal_rect)
    monkeypatch.setattr(windows, "_is_maximised", lambda h: fake.win[h]["state"] == "max")
    monkeypatch.setattr(windows, "_is_minimised", lambda h: fake.win[h]["state"] == "min")
    monkeypatch.setattr(windows, "_show", fake.show)
    monkeypatch.setattr(windows, "_set_pos", fake.set_pos)
    monkeypatch.setattr(windows, "_work_area", lambda h: fake.win[h]["work"])
    monkeypatch.setattr(windows, "_frame_border", lambda h: (0, 0, 0, 0))
    monkeypatch.setattr(windows, "_runs_as_admin", lambda h: fake.win[h]["admin"])
    monkeypatch.setattr(system_control, "_bring_to_front", lambda h: fake.calls.append(("front", h)) or True)
    return fake


def max_agent(provider=None):
    return Agent(dict(CONFIG), provider=provider)


# ---------- placement maths ----------

@pytest.mark.parametrize("work, side, border, rect", [
    (WORK, "left", (0, 0, 0, 0), (0, 0, 960, 1040)),
    (WORK, "right", (0, 0, 0, 0), (960, 0, 960, 1040)),
    (WORK, "left", (7, 0, 7, 7), (-7, 0, 974, 1047)),     # the invisible frame is added back
    (SECOND, "right", (0, 0, 0, 0), (2880, 0, 960, 1040)),
])
def test_half(work, side, border, rect):
    assert windows.half(work, side, border) == rect


@pytest.mark.parametrize("said, pair", [
    ("put vs code on the left and chrome on the right", ("vs code", "chrome")),
    ("put chrome on the right and vs code on the left", ("vs code", "chrome")),
    ("vs code left chrome right", ("vs code", "chrome")),
    ("notepad on the left side and calculator on the right side", ("notepad", "calculator")),
    ("put notepad and calculator side by side", ("notepad", "calculator")),
    ("chrome left and vs code left", None),
    ("close chrome and open notepad", None),
    # opening or closing two apps is two actions for the agent, not an arrange
    ("open chrome on the left and vs code on the right", None),
    ("close notepad on the left and chrome on the right", None),
    ("open notepad and chrome side by side", None),
])
def test_side_by_side_args(said, pair):
    assert side_by_side_args(said) == pair


@pytest.mark.parametrize("said, command", [
    ("list my windows", "list windows"),
    ("what windows are open", "list windows"),
    ("show desktop", "show desktop"),
    ("minimise everything", "show desktop"),
    ("put vs code on the left and chrome on the right", "side by side"),
    ("snap chrome left", "snap window"),
    ("put notepad on the right side", "snap window"),
    ("minimize chrome", "minimise"),
    ("maximise vs code", "maximise"),
    ("make chrome full screen", "maximise"),
    ("restore notepad", "restore window"),
    ("switch to discord", "focus window"),
    ("bring chrome to the front", "focus window"),
    ("switch to high performance", "set power plan"),   # still the power plan
    ("open new window", "new window"),                  # still a new window
])
def test_window_phrases(said, command):
    assert max_agent().match(said)[0].name == command


def test_open_two_apps_on_two_sides_goes_to_the_agent():
    provider = FakeProvider([Reply(text="Opened both.")])
    assert max_agent(provider).handle("open chrome on the left and vs code on the right").message == "Opened both."


def test_bring_needs_to_the_front():
    agent = max_agent()
    command, _ = agent.match("bring me some water")
    assert command is None or command.name != "focus window"
    assert agent.match("bring up discord")[0].name == "focus window"
    assert agent.match("bring the chrome window forward")[1] == "chrome"


def test_arrange_stays_local_with_a_brain(screen):
    screen.add(1, "code.exe", "desktop_agent.py - KOVA - Visual Studio Code")
    screen.add(2, "chrome.exe", "YouTube - Google Chrome")
    provider = FakeProvider()
    result = max_agent(provider).handle("put VS Code on the left and Chrome on the right")
    assert result.success and provider.fake_session.sent == []
    assert result.message == "Vs code is on the left and chrome on the right."
    assert screen.win[1]["pos"] == (0, 0, 960, 1040) and screen.win[2]["pos"] == (960, 0, 960, 1040)


# ---------- moving ----------

def test_snap_restores_a_maximised_window_first_and_undo_puts_it_back(screen):
    screen.add(1, "chrome.exe", "YouTube - Google Chrome", state="max")
    agent = max_agent()
    result = agent.handle("snap chrome left")
    assert result.success and result.message == "Chrome is on the left."
    assert ("show", 1, windows.SW_RESTORE) in screen.calls
    assert screen.calls[-1] == ("pos", 1, (0, 0, 960, 1040), "normal")  # moved after the restore
    assert agent.handle("undo").success
    assert screen.win[1]["state"] == "max" and screen.win[1]["pos"] == (100, 100, 800, 600)


def test_side_by_side_uses_the_first_apps_screen(screen):
    screen.add(1, "notepad.exe", "notes.txt - Notepad", work=SECOND)
    screen.add(2, "chrome.exe", "Gmail - Google Chrome", state="min")
    agent = max_agent()
    assert agent.handle("put notepad on the left and chrome on the right").success
    assert screen.win[1]["pos"] == (1920, 0, 960, 1040)
    assert screen.win[2]["pos"] == (2880, 0, 960, 1040) and screen.win[2]["state"] == "normal"
    agent.handle("undo")
    assert screen.win[2]["state"] == "min" and screen.win[1]["pos"] == (100, 100, 800, 600)


def test_admin_window_is_named_not_moved(screen):
    screen.add(1, "taskmgr.exe", "Task Manager", admin=True)
    result = windows.move("task manager", "left")
    assert result.message == "I can't move the task manager window, it's running as admin."
    assert screen.calls == []


def test_undo_still_puts_the_second_window_back_when_the_first_was_closed(screen):
    screen.add(1, "notepad.exe", "notes.txt - Notepad")
    screen.add(2, "chrome.exe", "Gmail - Google Chrome", state="max")
    agent = max_agent()
    agent.handle("put notepad on the left and chrome on the right")
    screen.win[1]["closed"] = True
    result = agent.handle("undo")
    assert not result.success and "a window may be closed" in result.message
    assert ("placement", 2) in screen.calls and screen.win[2]["state"] == "max"


def test_undo_after_windows_own_snap_goes_back_to_the_snapped_place(screen):
    # Snapped left with Win+Left: the placement still holds the floating rectangle
    screen.add(1, "chrome.exe", "YouTube - Google Chrome", real=(0, 0, 960, 1040))
    agent = max_agent()
    assert agent.handle("put chrome on the right").success
    agent.handle("undo")
    assert screen.win[1]["pos"] == (0, 0, 960, 1040)


def test_unknown_elevation_tries_the_move(screen, monkeypatch):
    screen.add(1, "game.exe", "Valorant")
    monkeypatch.setattr(windows, "_runs_as_admin", lambda h: None)   # Windows won't say
    assert windows.move("valorant", "left").message == "Valorant is on the left."


def test_unknown_elevation_that_fails_gets_the_admin_message(screen, monkeypatch):
    screen.add(1, "game.exe", "Valorant", blocked=True)
    monkeypatch.setattr(windows, "_runs_as_admin", lambda h: None)
    assert windows.move("valorant", "left").message == "I can't move the valorant window, it's running as admin."


def test_a_known_normal_window_that_fails_says_so_plainly(screen):
    screen.add(1, "notepad.exe", "notes.txt - Notepad", blocked=True)
    assert windows.move("notepad", "right").message == "Windows didn't let me move notepad."


def test_unknown_or_closed_app(screen):
    result = max_agent().handle("minimise notepad")
    assert not result.success and result.message == "I can't see a notepad window."


def test_minimise_it_means_the_last_app(screen):
    screen.add(1, "chrome.exe", "YouTube - Google Chrome")
    agent = max_agent()
    agent.runner.last_target = "chrome"
    assert agent.handle("minimise it").message == "Minimised chrome."
    assert screen.win[1]["state"] == "min"


def test_put_it_on_the_right(screen):
    screen.add(1, "notepad.exe", "notes.txt - Notepad")
    agent = max_agent()
    agent.runner.last_target = "notepad"
    assert agent.handle("put it on the right").message == "Notepad is on the right."


def test_a_window_found_by_its_title(screen):
    screen.add(1, "discord.exe", "#general | KOVA - Discord")
    screen.add(2, "chrome.exe", "Lofi Hip Hop Radio - YouTube - Google Chrome")
    assert windows.move("lofi music", "maximise").message == "Maximised lofi music."
    assert screen.win[2]["state"] == "max"
    assert windows.move("discord", "front").success and ("front", 1) in screen.calls


def test_pick_one_of_several_windows_by_title(screen):
    screen.add(1, "chrome.exe", "YouTube - Google Chrome")
    screen.add(2, "chrome.exe", "Gmail - Google Chrome")
    assert windows.move("chrome", "minimise").success and screen.win[1]["state"] == "min"   # the front one
    assert windows.move("chrome", "minimise", "gmail").success and screen.win[2]["state"] == "min"


def test_switch_to_something_that_is_not_a_window_asks_the_agent(screen):
    provider = FakeProvider([Reply(text="Dark mode is on.")])
    assert max_agent(provider).handle("switch to dark mode").message == "Dark mode is on."


def test_list_windows(screen):
    screen.add(1, "code.exe", "desktop_agent.py - KOVA - Visual Studio Code")
    screen.add(2, "chrome.exe", "(3) Excuses (Official Video) | AP Dhillon - YouTube - Google Chrome")
    result = max_agent().handle("list my windows")
    assert result.message == "2 windows are open: vs code, desktop_agent.py - KOVA; chrome, Excuses."


def test_show_desktop_and_undo(screen, monkeypatch):
    pressed = []
    monkeypatch.setattr(windows, "_press_win_d", lambda: pressed.append("win+d"))
    agent = max_agent()
    assert agent.handle("show the desktop").success
    assert agent.handle("undo").success and pressed == ["win+d", "win+d"]


def test_close_a_window_apps_json_doesnt_know(screen, monkeypatch):
    # Found in the live test: "close wps photos window" -> "I don't know an app called ..."
    from modules import ui_automation
    screen.add(5, "wpsphotos.exe", "pic.png - WPS Photos")
    closed = []
    monkeypatch.setattr(ui_automation, "_is_terminal", lambda h: False)
    monkeypatch.setattr(system_control, "_post_close", lambda hwnds: closed.extend(hwnds))
    monkeypatch.setattr(system_control, "_window_title", lambda h: None if h in closed else "x")
    asked = []
    agent = Agent(dict(CONFIG), confirm=lambda q: asked.append(q) or True)
    result = agent.handle("close wps photos window")
    assert asked == ["Close the wpsphotos window pic.png?"]
    assert result.success and result.message == "Closed the wpsphotos window, pic.png." and closed == [5]


def test_close_window_never_closes_a_terminal(screen, monkeypatch):
    from modules import ui_automation
    screen.add(5, "cmd.exe", "Claude Code")
    monkeypatch.setattr(ui_automation, "_is_terminal", lambda h: True)
    assert "terminal" in windows.close_window("claude code").message


def test_open_folder_said_with_now():
    assert max_agent().match("open my assembly language folder now")[0].name == "open folder"


def test_folders_said_outside_in():
    assert system_control._outside_in("learning assembly in coding arc folder in d drive") == \
        "d drive coding arc learning assembly"
    assert system_control._outside_in("notes in urdu") is None


def test_the_agent_can_move_windows(screen):
    from modules import tool_registry
    names = {t.name for t in tool_registry.all_tools()}
    assert {"open_windows", "move_window", "side_by_side", "show_desktop"} <= names
    assert tool_registry.get("move_window").level == "safe"


def test_spoken_path_takes_a_drive_root_and_backslashes(tmp_path):
    # Voice test: "open d drive" -> "I can't find a folder called D:\" ("[\/]" matched only "/")
    import os
    drive = os.path.splitdrive(str(tmp_path))[0]
    assert system_control.spoken_path(drive + "\\") == drive + "\\"
    assert system_control.spoken_path(str(tmp_path)) == str(tmp_path)


def test_close_one_of_two_tabs_with_the_same_title(monkeypatch):
    # Voice test: two WhatsApp tabs; Ctrl+W closed one, the other showed the same title, and Max
    # said "still open"
    monkeypatch.setattr(system_control, "_resolve_app", lambda name: ("chrome", {"process": ["chrome.exe"]}))
    monkeypatch.setattr(system_control, "app_windows", lambda procs: [(1, "WhatsApp - Google Chrome")])
    monkeypatch.setattr(system_control, "_bring_to_front", lambda h: True)
    monkeypatch.setattr(system_control, "_press_ctrl_w", lambda: None)
    monkeypatch.setattr(system_control, "_window_title", lambda h: "WhatsApp - Google Chrome")
    monkeypatch.setattr(system_control.time, "sleep", lambda s: None)
    counts = iter([5, 4])
    monkeypatch.setattr(system_control, "_tab_count", lambda h: next(counts))
    result = system_control.close_tab("chrome", "WhatsApp", wait_seconds=0)
    assert result.success and "Another WhatsApp tab is showing now" in result.message


def test_the_log_hides_one_time_codes():
    # Voice test: "Login Code: 300253 - Gmail" went into kova.log with a window title
    import logging
    from modules.logger import MaskingFormatter
    record = logging.LogRecord("kova", logging.INFO, "", 0, "Window chrome (Login Code: %s - Gmail) -> minimise",
                               ("300253",), None)
    line = MaskingFormatter("%(message)s").format(record)
    assert "300253" not in line and "Login Code: •••••" in line
    record = logging.LogRecord("kova", logging.INFO, "", 0, "RAM 4096 MB in 2026, fact 18", (), None)
    assert MaskingFormatter("%(message)s").format(record) == "RAM 4096 MB in 2026, fact 18"
