"""'maximise your window' with the dashboard hidden — regression tests for
what Bilal saw on 10 Nov: the only own window still visible was the pet
companion, so Win32 stretched the round, frameless persona black into the
top-left corner and it stuck there while the dashboard never came back.

Contract: self-referential window words ("your", "yourself", "kova", "max",
"mira"...) mean the dashboard and only the dashboard. A visible dashboard
moves the normal Win32 way; a hidden one comes back only through the UI layer
(run.py wires windows.on_own_window), because pywebview owns its show/hide
state and the tray's is_visible follows it. Companions are never moved.
"""
import os

import pytest

from desktop_agent import Agent
from modules import windows
from modules.result import Result


def fake_own(monkeypatch, dashboard=None, pet=None, dashboard_visible=True):
    """Own windows: the dashboard ('KOVA — Max') and the pet ('KOVA companion').

    dashboard_visible=False is the X-hidden dashboard: not in visible_windows()
    and IsWindowVisible False, exactly like a real Hide()."""
    own = []
    if dashboard:
        own.append((dashboard, "KOVA — Max"))
    if pet:
        own.append((pet, "KOVA companion"))
    monkeypatch.setattr(windows, "_own_windows_with_titles", lambda: own)
    monkeypatch.setattr(windows, "_is_visible", lambda hwnd: dashboard_visible or hwnd != dashboard)
    monkeypatch.setattr(windows, "visible_windows", lambda: [
        (hwnd, "python.exe", title) for hwnd, title in own
        if hwnd != dashboard or dashboard_visible])
    monkeypatch.setattr(windows, "_ours", lambda hwnd: any(hwnd == h for h, _ in own))


def win32_recorder(monkeypatch):
    """The Win32 layer, recorded instead of done: every touched hwnd shows up here."""
    touched = []
    monkeypatch.setattr(windows, "_save", lambda hwnd: (hwnd, None, None))
    monkeypatch.setattr(windows, "_show", lambda hwnd, cmd: touched.append((hwnd, cmd)) or True)
    monkeypatch.setattr(windows, "_runs_as_admin", lambda hwnd: False)
    return touched


def hook_recorder():
    calls = []
    def hook(position):
        calls.append(position)
        return Result(True, "Bringing my window back up.")
    return calls, hook


@pytest.mark.parametrize("said", ["your", "your window", "yourself", "kova"])
def test_maximise_your_window_when_hidden_asks_the_ui_layer(monkeypatch, said):
    fake_own(monkeypatch, dashboard=10, pet=11, dashboard_visible=False)
    touched = win32_recorder(monkeypatch)
    calls, hook = hook_recorder()
    windows.on_own_window = hook
    try:
        result = windows.move(said, "maximise")
        assert result.success and calls == ["maximise"]     # the UI layer is asked
        assert touched == []                                # no Win32 on the hidden window
    finally:
        windows.on_own_window = None


def test_your_window_never_means_the_pet(monkeypatch):
    fake_own(monkeypatch, pet=11)                            # voice mode: no dashboard at all
    touched = win32_recorder(monkeypatch)
    result = windows.move("your", "maximise")
    assert not result.success and "isn't open" in result.message
    assert touched == []                                     # the pet was not touched


def test_maximise_your_window_when_visible_uses_the_normal_way(monkeypatch):
    fake_own(monkeypatch, dashboard=10, pet=11)              # dashboard on screen
    touched = win32_recorder(monkeypatch)
    result = windows.move("your", "maximise")
    assert touched == [(10, windows.SW_MAXIMIZE)]            # the dashboard, never the pet
    assert result.success and result.undo is not None        # undo still offered


def test_minimise_your_window_when_hidden_is_already_done(monkeypatch):
    fake_own(monkeypatch, dashboard=10, dashboard_visible=False)
    calls, hook = hook_recorder()
    windows.on_own_window = hook
    try:
        result = windows.move("your", "minimise")
        assert result.success and calls == []                # nothing to ask the UI for
        assert "out of sight" in result.message
    finally:
        windows.on_own_window = None


def test_hidden_dashboard_without_a_ui_hook_says_so(monkeypatch):
    fake_own(monkeypatch, dashboard=10, dashboard_visible=False)
    assert windows.on_own_window is None                     # voice/text mode default
    result = windows.move("your", "front")
    assert not result.success and "can't bring it back" in result.message


def test_focus_your_window_when_hidden_goes_to_the_ui_layer(monkeypatch):
    fake_own(monkeypatch, dashboard=10, dashboard_visible=False)
    calls, hook = hook_recorder()
    windows.on_own_window = hook
    try:
        assert windows.move("your", "front").success and calls == ["front"]
    finally:
        windows.on_own_window = None


def test_find_your_window_skips_the_pet_companion(monkeypatch):
    """The old bug walked through find(): with the dashboard hidden it returned
    the pet as 'your window'."""
    fake_own(monkeypatch, pet=11)
    hwnd, app, answer = windows.find("your")
    assert hwnd is None and not answer.success


def test_find_your_window_is_the_dashboard_when_both_are_up(monkeypatch):
    fake_own(monkeypatch, dashboard=10, pet=11)
    hwnd, app, title = windows.find("your")
    assert (hwnd, app) == (10, "KOVA")


def test_a_companion_refuses_generic_moves(monkeypatch):
    """'maximise the companion window' finds the pet by its title words — and
    must refuse, because a maximised frameless round window breaks."""
    fake_own(monkeypatch, dashboard=10, pet=11)
    touched = win32_recorder(monkeypatch)
    result = windows.move("companion", "maximise")
    assert not result.success and touched == []
    assert "companion" in result.message


@pytest.mark.parametrize("phrase, arg", [("maximize your window", "your"),
                                         ("maximize yourself", "yourself"),
                                         ("maximise yourself", "yourself")])
def test_the_spoken_command_routes_to_your_window(phrase, arg, monkeypatch):
    """Both exact phrases from Bilal's 10 Nov retest behave identically: a
    built-in maximise command whose target is the dashboard via move_window.
    The command table captures Agent._tool when the agent is built, so the
    tool layer is patched first."""
    used = []
    def fake_tool(self, tool_name, **args):
        used.append((tool_name, args))
        return Result(True, "ok")
    monkeypatch.setattr(Agent, "_tool", fake_tool)   # restored after the test
    max_agent = Agent({"ASSISTANT_NAME": "Max", "WAKE_WORDS": ["max"]})
    command, heard = max_agent.match(phrase)
    assert command.name == "maximise" and heard == arg
    command.handler(heard)
    assert used == [("move_window", {"name": arg, "position": "maximise"})]


def test_is_dashboard_title_locks_the_prefix():
    """The dashboard is 'KOVA — <persona>' (em dash), the companion is never
    'KOVA — ...' — this is what keeps the Reaper out of self-referential moves."""
    assert windows._is_dashboard_title("KOVA — Max") is True
    assert windows._is_dashboard_title("KOVA — Mira") is True
    assert windows._is_dashboard_title("KOVA companion") is False
    assert windows._is_dashboard_title("KOVA") is False
    assert windows._is_dashboard_title("") is False
    assert windows._is_dashboard_title(None) is False


_REAL_OURS = windows._ours   # conftest patches _ours off; the real test needs it back


@pytest.mark.skipif(os.name != "nt", reason="needs a real Win32 window")
def test_own_window_enumeration_reads_real_titles(monkeypatch):
    """The live regression from 10 Nov: the enumeration passed the title buffer
    size where the buffer belongs, GetWindowTextW returned nothing, every own
    window looked title-less, and 'maximise yourself' answered "My window
    isn't open" while the UI was running. This test creates a real window of
    the test process and demands the real title back through the exact path
    _move_own() uses."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND, wintypes.UINT,
                                 wintypes.WPARAM, wintypes.LPARAM)
    user32.DefWindowProcW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.DefWindowProcW.restype = ctypes.c_longlong

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                    ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                    ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

    monkeypatch.setattr(windows, "_ours", _REAL_OURS)   # the real pid check, not conftest's stub

    hinstance = kernel32.GetModuleHandleW(None)
    wndproc = WNDPROC(user32.DefWindowProcW)            # held for the whole test: Windows calls it
    wc = WNDCLASSW()
    wc.lpfnWndProc = wndproc
    wc.hInstance = hinstance
    wc.lpszClassName = f"KovaTitleProbe{os.getpid()}"
    assert user32.RegisterClassW(ctypes.byref(wc))
    hwnd = None
    try:
        title = "KOVA — TestProbe"                      # the em dash the dashboard uses
        hwnd = user32.CreateWindowExW(0, wc.lpszClassName, title, 0x00CF0000,
                                      0, 0, 200, 100, None, None, hinstance, None)
        assert hwnd

        own = windows._own_windows_with_titles()
        assert dict(own).get(hwnd) == title             # a real title, not '' or 0
        dash, dash_title = windows._dashboard_hwnd()
        assert dash == hwnd and dash_title == title     # _move_own() finds the dashboard
    finally:
        if hwnd:
            user32.DestroyWindow(hwnd)
        user32.UnregisterClassW(wc.lpszClassName, hinstance)
