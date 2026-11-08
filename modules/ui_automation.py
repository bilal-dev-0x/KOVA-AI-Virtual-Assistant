import re
import time
from dataclasses import dataclass

from modules import system_control, windows
from modules.logger import get_logger
from modules.result import Result

# Windows UI Automation: the real buttons, menus and fields of the window in front, by name.
# "what can I click here?", "click Save", "type hello into the search box", "press ctrl s".
#
# Safety:
#   - only the window in front, never the terminal Max runs in; not an admin or a hung window;
#   - a name must match exactly one control, else Max lists them and stops;
#   - right before acting, the window must still be the one that was read (else the plan halts);
#   - a button that can't be taken back (Delete, Send, Pay...) is asked about again right before;
#   - password fields are never typed into, existing text is never replaced, and a one-line box
#     never gets a new line (Enter would send the message half written);
#   - keys: a short list of everyday shortcuts only.
# Reading stops after READ_SECONDS and MAX_DEPTH: Chrome and Electron apps have huge trees.
#
# The COM and keyboard calls are small functions at the bottom, so tests can replace them.

log = get_logger()

READ_SECONDS = 2.0
MAX_DEPTH = 12
MAX_CONTROLS = 300
MAX_TEXT = 2000

# UI Automation control type IDs
KINDS = {50000: "button", 50002: "checkbox", 50003: "dropdown", 50004: "field", 50005: "link", 50007: "list item",
         50011: "menu", 50013: "option", 50019: "tab", 50024: "tree item", 50030: "text area", 50031: "button"}
EDITABLE = {"field", "text area"}
# "type hello into the text area": no name, the window's one text box
GENERIC_FIELDS = {"", "text", "text area", "text box", "textbox", "box", "field", "editor", "document", "page",
                  "it", "the text area", "the box", "the field"}
# Clicking these can't be taken back: asked about again right before the click
RISKY = ["delete", "remove", "uninstall", "send", "pay", "buy", "purchase", "order", "checkout", "confirm",
         "format", "reset", "erase", "empty", "sign out", "log out", "discard", "overwrite", "transfer"]
# Typing or pressing Enter in a terminal runs a command, and Max never runs commands
TERMINAL_PROCESSES = {"cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe", "wt.exe", "conhost.exe",
                      "openconsole.exe", "wsl.exe", "bash.exe", "mintty.exe", "alacritty.exe", "wezterm-gui.exe"}
TERMINAL_CLASSES = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "PseudoConsoleWindow", "mintty",
                    "VirtualConsoleClass"}
KEYS = {"enter": ["enter"], "return": ["enter"], "escape": ["escape"], "esc": ["escape"], "tab": ["tab"],
        "backspace": ["backspace"],
        "ctrl+s": ["ctrl", "s"], "ctrl+a": ["ctrl", "a"], "ctrl+c": ["ctrl", "c"], "ctrl+v": ["ctrl", "v"],
        "ctrl+z": ["ctrl", "z"], "ctrl+f": ["ctrl", "f"], "ctrl+n": ["ctrl", "n"], "ctrl+t": ["ctrl", "t"]}


@dataclass
class Control:
    name: str
    kind: str
    element: object = None
    password: bool = False


def spoken_name(name):
    """'&Save...' -> 'save'; 'File name:' -> 'file name'."""
    return " ".join(re.sub(r"[&:.…]+", " ", name or "").lower().split())


def risky(name):
    said = f" {spoken_name(name)} "
    return next((word for word in RISKY if f" {word} " in said), None)


def key_combo(said):
    """'control s', 'ctrl+S', 'Ctrl S' -> 'ctrl+s'; 'enter' -> 'enter'; None if not allowed."""
    said = re.sub(r"\s*\+\s*|\s+", "+", (said or "").strip().lower()).replace("control", "ctrl")
    said = said.removesuffix("+key").replace("back+space", "backspace")
    return said if said in KEYS else None


# ---------- the window ----------

def _target(window="", acting=True):
    """(hwnd, app, title) of the window in front (not Max's terminal) that Max may work in,
    or a Result saying why not. acting: click, type or press keys (reading is always fine)."""
    found = windows.target_window()
    if found is None:
        return Result(False, "I can't see a window to work in.")
    hwnd, app, title = found
    if acting and _is_terminal(hwnd):
        # In text mode the window behind Max's own is often another terminal (even Claude Code's)
        return Result(False, f"The window in front is a terminal ({system_control.spoken_title(title)}). I never "
                             "type, click or press keys in a terminal, because that would run commands. "
                             "Click the window you mean first.", halt=True)
    if window and not system_control.title_has(title, system_control.title_words(window)):
        return Result(False, f"{system_control.spoken_title(title)} is in front, not {window}, so I stopped.",
                      halt=True)
    if windows._runs_as_admin(hwnd) is True:
        return Result(False, f"{app} is running as admin, so Windows doesn't let me click or type in it.",
                      halt=True)
    if _is_hung(hwnd):
        return Result(False, f"{app} isn't responding, so I can't read it.", halt=True)
    return found


def _same_window(hwnd, title):
    """None while the window that was read is still the one in front with the same title, else a
    halting Result. A dialog popping up over it (Save changes?) counts as a change."""
    now = system_control._window_title(hwnd)
    if now is None:
        return Result(False, "The window closed, so I stopped.", halt=True)
    if now != title:
        return Result(False, f"The window changed to {system_control.spoken_title(now)}, so I stopped.", halt=True)
    front = windows.target_window()
    if front is None or front[0] != hwnd:
        shown = system_control.spoken_title(front[2]) if front else "something else"
        return Result(False, f"{shown} popped up in front, so I stopped.", halt=True)
    return None


def read_controls(hwnd):
    """The named controls of a window, within READ_SECONDS and MAX_DEPTH."""
    return _read_tree(hwnd, time.time() + READ_SECONDS, MAX_DEPTH)


def describe():
    target = _target(acting=False)
    if isinstance(target, Result):
        return target
    hwnd, app, title = target
    controls = [c for c in read_controls(hwnd) if c.name or c.kind in EDITABLE]
    if not controls:
        return Result(True, f"I can't read any buttons or fields in {app}. It may draw its own controls.")
    groups = {}
    for c in controls:
        groups.setdefault(c.kind, []).append(c.name or "(no name)")
    said = []
    for kind, names in groups.items():
        unique = list(dict.fromkeys(names))
        said.append(f"{kind}s: {', '.join(unique[:6])}" + (f" and {len(unique) - 6} more" if len(unique) > 6 else ""))
    message = f"In {app}, {system_control.spoken_title(title)}, I can see " + "; ".join(said[:5]) + "."
    detail = "\n".join(f"  {c.kind:<10} {c.name}" for c in controls[:100])
    return Result(True, message, detail=detail)


# Words that say what kind of control it is, not its name: "click the chat Rehan" means "Rehan"
CONTROL_WORDS = {"the", "a", "an", "chat", "chats", "conversation", "contact", "button", "link", "tab", "icon",
                 "option", "item", "menu", "entry", "on", "with", "named", "called"}


def _matches(controls, name, editable=False):
    """Controls whose name is the one said: exact first, else every word in it, else every word
    that isn't just the kind of control ("chat", "button")."""
    wanted = spoken_name(name)
    pool = [c for c in controls if (c.kind in EDITABLE) == editable]
    exact = [c for c in pool if spoken_name(c.name) == wanted]
    if exact:
        return exact
    for words in (wanted.split(), [w for w in wanted.split() if w not in CONTROL_WORDS]):
        found = [c for c in pool if words and all(w in spoken_name(c.name).split() for w in words)]
        if found:
            return found
    return []


def _one(controls, name, app, editable):
    """The single control name means, or a halting Result: none, or several (listed)."""
    found = _matches(controls, name, editable)
    if not found and editable and spoken_name(name) in GENERIC_FIELDS:
        found = [c for c in controls if c.kind in EDITABLE]
        # A Save As dialog has a search box and name columns too, but "the field" there means the file name
        file_name = [c for c in found if spoken_name(c.name) == "file name"]
        if len(found) > 1 and len(file_name) == 1:
            found = file_name
        if len(found) > 1:
            names = ", ".join(c.name or "no name" for c in found[:5])
            return Result(False, f"{app} has {len(found)} text fields: {names}. Say which one.", halt=True)
    if not found:
        what = "field" if editable else "control"
        if not spoken_name(name):
            # 7 Nov log: "called  in ytdesktop"; apps drawn without real controls (Electron) show none
            return Result(False, f"I can't find a text {what} I can type in, in {app}. Click the search box "
                                 "first, then tell me what to type.", halt=True)
        return Result(False, f"I can't find a {what} called {name} in {app}.", halt=True)
    if len(found) > 1:
        names = "; ".join(f"{c.kind} {c.name}" for c in found[:5])
        return Result(False, f"There are {len(found)} called {name} in {app}: {names}. Say which one.", halt=True)
    return found[0]


# ---------- acting ----------

def click(name, window="", confirm=None):
    target = _target(window)
    if isinstance(target, Result):
        return target
    hwnd, app, title = target
    control = _one(read_controls(hwnd), name, app, editable=False)
    if isinstance(control, Result):
        return control
    word = risky(control.name)
    if word:
        # Like a critical step: one more question, right before, whoever asked for the click
        if not (confirm and confirm(f"Click {control.name} in {app}? It can't be undone.")):
            return Result(False, f"Okay, I didn't click {control.name}.", halt=True)
    problem = _same_window(hwnd, title)  # checked again right before the click
    if problem:
        return problem
    if not _invoke(control):
        return Result(False, f"{control.name} in {app} didn't respond to a click.", halt=True)
    log.info("Clicked %s %r in %s", control.kind, control.name, app)
    return Result(True, f"Clicked {control.name}.")


def type_text(text, field="", window=""):
    if len(text) > MAX_TEXT:
        return Result(False, f"That's {len(text)} characters, too long to type. Paste it instead.")
    target = _target(window)
    if isinstance(target, Result):
        return target
    hwnd, app, title = target
    control = _one(read_controls(hwnd), field, app, editable=True)
    if isinstance(control, Result):
        return control
    shown = control.name or "the text field"
    if control.password:
        return Result(False, "That's a password field. I never type into those; please type it yourself.", halt=True)
    if "\n" in text and control.kind != "text area":
        return Result(False, f"{shown} is one line, and a new line would press Enter and send it. "
                             "Type the lines one at a time, or say press enter yourself.")
    problem = _same_window(hwnd, title)
    if problem:
        return problem
    if _is_file_name_box(hwnd, control):
        # A Save / Open dialog's File name box holds what Windows put there ("*.txt", "Untitled"),
        # not your writing: the name replaces it, or "Untitled" and the name would run together
        if not _replace_value(control, text):
            return Result(False, "I couldn't fill in the file name.", halt=True)
    # An empty field gets the text as its value; one with text in it gets it typed at the cursor,
    # so nothing you wrote is replaced
    elif not _set_empty_value(control, text):
        if not _focus(hwnd, control):
            return Result(False, f"I couldn't put the cursor in {shown}.", halt=True)
        if not _still_focused(control):  # focus moved (a popup?): type nothing anywhere else
            return Result(False, f"The cursor left {shown}, so I didn't type.", halt=True)
        if not _type_keys(text):
            return Result(False, f"Windows blocked the typing in {app}. It may be running as admin, "
                                 "or the screen is locked.", halt=True)
    log.info("Typed %s characters into %r in %s", len(text), control.name, app)
    return Result(True, f"Typed it into {shown} in {app}.")


def press_keys(keys, window=""):
    combo = key_combo(keys)
    if combo is None:
        return Result(False, f"I only press everyday keys: {', '.join(sorted(KEYS))}.")
    target = _target(window)
    if isinstance(target, Result):
        return target
    hwnd, app, title = target
    if not system_control._bring_to_front(hwnd):
        return Result(False, f"Windows didn't let me bring {app} to the front, so I didn't press anything.", halt=True)
    problem = _same_window(hwnd, title)
    if problem:
        return problem
    if not _press(KEYS[combo]):
        return Result(False, f"Windows blocked the key press in {app}.", halt=True)
    log.info("Pressed %s in %s", combo, app)
    return Result(True, f"Pressed {combo} in {app}.")


# ---------- UI Automation through comtypes (replaced in tests) ----------

_UIA = {}


def _automation():
    """IUIAutomation, its type library and a cache request. comtypes builds the wrapper once
    and keeps it in its cache; timeouts keep a hung app from blocking Max."""
    if not _UIA:
        import comtypes.client
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as UIA
        try:
            # CUIAutomation8 (Windows 8+) has the timeouts; the plain one doesn't
            uia = comtypes.client.CreateObject(UIA.CUIAutomation8, interface=UIA.IUIAutomation2)
            uia.ConnectionTimeout = 1000   # ms to reach the app
            uia.TransactionTimeout = 2000  # ms per question to it
        except Exception:
            # Older Windows: READ_SECONDS and the hung-window check still apply
            uia = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
        # One round trip per element for everything Max needs, not five
        cache = uia.CreateCacheRequest()
        for prop in (UIA.UIA_NamePropertyId, UIA.UIA_ControlTypePropertyId, UIA.UIA_IsOffscreenPropertyId,
                     UIA.UIA_IsEnabledPropertyId, UIA.UIA_IsPasswordPropertyId):
            cache.AddProperty(prop)
        _UIA.update(uia=uia, lib=UIA, cache=cache)
    return _UIA["uia"], _UIA["lib"]


def _is_terminal(hwnd):
    """True for a console or terminal window (by its window class or its program)."""
    if _window_class(hwnd) in TERMINAL_CLASSES:
        return True
    import ctypes
    import psutil
    from ctypes import wintypes
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(windows._h(hwnd), ctypes.byref(pid))
    try:
        return psutil.Process(pid.value).name().lower() in TERMINAL_PROCESSES
    except (psutil.Error, ValueError):
        return True  # can't tell: don't risk typing into it


def _is_hung(hwnd):
    import ctypes
    return bool(ctypes.windll.user32.IsHungAppWindow(windows._h(hwnd)))


def _read_tree(hwnd, deadline, max_depth):
    """Breadth first, so the top of the window (menus, toolbar) comes first; stops at the deadline."""
    uia, _ = _automation()
    cache, walker = _UIA["cache"], uia.ControlViewWalker
    found, queue = [], [(uia.ElementFromHandle(hwnd), 0)]
    while queue and time.time() < deadline and len(found) < MAX_CONTROLS:
        element, depth = queue.pop(0)
        try:
            child = walker.GetFirstChildElementBuildCache(element, cache)
        except Exception:
            continue
        while child and time.time() < deadline:
            try:
                kind = KINDS.get(child.CachedControlType)
                if kind and not child.CachedIsOffscreen and child.CachedIsEnabled:
                    found.append(Control(child.CachedName or "", kind, child, bool(child.CachedIsPassword)))
                if depth < max_depth:
                    queue.append((child, depth + 1))
                child = walker.GetNextSiblingElementBuildCache(child, cache)
            except Exception:
                break
    return found


def _pattern(element, pattern_id, interface):
    pattern = element.GetCurrentPattern(pattern_id)
    return pattern.QueryInterface(interface) if pattern else None


def _invoke(control):
    """Click by the control's own action: Invoke, else Toggle, Select or Expand."""
    _, UIA = _automation()
    element = control.element
    for pattern_id, interface, action in (
            (UIA.UIA_InvokePatternId, UIA.IUIAutomationInvokePattern, "Invoke"),
            (UIA.UIA_TogglePatternId, UIA.IUIAutomationTogglePattern, "Toggle"),
            (UIA.UIA_SelectionItemPatternId, UIA.IUIAutomationSelectionItemPattern, "Select"),
            (UIA.UIA_ExpandCollapsePatternId, UIA.IUIAutomationExpandCollapsePattern, "Expand")):
        try:
            pattern = _pattern(element, pattern_id, interface)
            if pattern:
                getattr(pattern, action)()
                return True
        except Exception:
            continue
    return False


def _set_empty_value(control, text):
    """Set the text as the field's value, only if the field is empty and writable."""
    _, UIA = _automation()
    try:
        value = _pattern(control.element, UIA.UIA_ValuePatternId, UIA.IUIAutomationValuePattern)
        if value is None or value.CurrentIsReadOnly or value.CurrentValue:
            return False
        value.SetValue(text)
        return True
    except Exception:
        return False


def _is_file_name_box(hwnd, control):
    """The File name box of Windows' own Save / Open dialog (window class #32770)."""
    return control.kind == "field" and spoken_name(control.name) == "file name" and _window_class(hwnd) == "#32770"


def _window_class(hwnd):
    import ctypes
    name = ctypes.create_unicode_buffer(64)
    ctypes.windll.user32.GetClassNameW(windows._h(hwnd), name, 64)
    return name.value


def _replace_value(control, text):
    """Set the field's value even if it has text, as long as it is writable."""
    _, UIA = _automation()
    try:
        value = _pattern(control.element, UIA.UIA_ValuePatternId, UIA.IUIAutomationValuePattern)
        if value is None or value.CurrentIsReadOnly:
            return False
        value.SetValue(text)
        return True
    except Exception:
        return False


def _focus(hwnd, control):
    """Bring the window to the front and the cursor into the field; True only if both happened."""
    if not system_control._bring_to_front(hwnd):
        return False
    try:
        control.element.SetFocus()
    except Exception:
        return False
    return _still_focused(control)


def _still_focused(control):
    """True if the keyboard focus is in this control (and it isn't a password field)."""
    try:
        uia, _ = _automation()
        focused = uia.GetFocusedElement()
        return bool(focused) and not focused.CurrentIsPassword and bool(uia.CompareElements(focused, control.element))
    except Exception:
        return False


def _input_type():
    import ctypes
    from ctypes import wintypes

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class MOUSEINPUT(ctypes.Structure):  # the largest member: sets the union's size
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class UNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", UNION)]
    return INPUT, KEYBDINPUT


def _send(events):
    """events: [(vk, scan, flags)]. True only if Windows took every one: UIPI (an admin window),
    a locked screen or the secure desktop take fewer."""
    import ctypes
    INPUT, KEYBDINPUT = _input_type()
    INPUT_KEYBOARD = 1
    inputs = (INPUT * len(events))()
    for i, (vk, scan, flags) in enumerate(events):
        inputs[i].type = INPUT_KEYBOARD
        inputs[i].u.ki = KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
    return ctypes.windll.user32.SendInput(len(events), inputs, ctypes.sizeof(INPUT)) == len(events)


def text_events(text):
    """Key events that type text as Unicode characters at the cursor (never as shortcuts)."""
    KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0002, 0x0004
    events = []
    for char in text.replace("\r\n", "\n"):
        if char == "\n":
            events += [(0x0D, 0, 0), (0x0D, 0, KEYEVENTF_KEYUP)]
            continue
        data = char.encode("utf-16-le")
        for unit in (int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)):
            events += [(0, unit, KEYEVENTF_UNICODE), (0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)]
    return events


def _type_keys(text):
    return _send(text_events(text))


VK = {"ctrl": 0x11, "enter": 0x0D, "escape": 0x1B, "tab": 0x09, "backspace": 0x08,
      **{c: ord(c.upper()) for c in "acfnstvz"}}


def _press(keys):
    KEYEVENTF_KEYUP = 0x0002
    return _send([(VK[k], 0, 0) for k in keys] + [(VK[k], 0, KEYEVENTF_KEYUP) for k in reversed(keys)])
