import os
import re
import time
from difflib import SequenceMatcher

from modules import system_control
from modules.logger import get_logger
from modules.result import Result

# Window control: list, bring to the front, minimise, maximise, restore, snap to a half of
# the screen and put two apps side by side. Windows' own calls through ctypes, nothing to
# install. Every move saves where the window was, so undo puts it back.
#
# The Win32 calls are small functions at the bottom, so tests can replace them.

log = get_logger()

SW_MAXIMIZE, SW_MINIMIZE, SW_RESTORE = 3, 6, 9
SIDES = ("left", "right")
POSITIONS = ("left", "right", "maximise", "minimise", "restore", "front")
MAX_LISTED = 8
ADMIN = "I can't move the {} window, it's running as admin."

# "chrome on the left", "vs code to the right side"
SIDE_OF = re.compile(r"^(?P<name>.+?)( window)?( on| to| at)?( the)? (?P<side>left|right)( side| half)?$")


def split_side(text):
    """'chrome on the left' -> ('chrome', 'left'); None without a side."""
    m = SIDE_OF.match(text or "")
    return (m["name"].strip(), m["side"]) if m else None


# ---------- placement maths (pure) ----------

def half(work, side, border=(0, 0, 0, 0)):
    """(x, y, width, height) that fills the left or right half of a work area.

    work is the screen minus the taskbar as (left, top, right, bottom). border is the
    window's invisible frame (left, top, right, bottom): Windows 10 draws a few pixels of
    shadow around a window, so without it a snapped window leaves a gap.
    """
    left, top, right, bottom = work
    middle = left + (right - left) // 2
    x0, x1 = (left, middle) if side == "left" else (middle, right)
    bl, bt, br, bb = border
    return x0 - bl, top - bt, (x1 - x0) + bl + br, (bottom - top) + bt + bb


# ---------- finding windows ----------

def app_name(process):
    """'Code.exe' -> 'vs code' when apps.json knows it, else 'code'."""
    lower = (process or "").lower()
    for key, entry in system_control.load_apps().get("apps", {}).items():
        if lower in {p.lower() for p in entry.get("process", [])}:
            return key
    return lower.removesuffix(".exe")


def _near_windows(name):
    """Find a unique open window matching a speech-misheard app name; never searches installed apps."""
    query = re.sub(r"\s+window$", "", (name or "").lower().strip())
    query = re.sub(r"[^a-z0-9 ]", " ", query)
    query = " ".join(query.split())
    if len(query) < 3:
        return []

    def consonants(value):
        return re.sub(r"[aeiou\W_]+", "", value.lower())

    matches = []
    for hwnd, process, title in visible_windows():
        if _ours(hwnd):
            continue
        app = app_name(process)
        names = (app, process.removesuffix(".exe"), title)
        score = 0.0
        phonetic = False
        for candidate in names:
            candidate = re.sub(r"[^a-z0-9 ]", " ", candidate.lower())
            candidate = " ".join(candidate.split())
            score = max(score, SequenceMatcher(None, query, candidate, autojunk=False).ratio())
            left, right = consonants(query), consonants(candidate)
            if left and right and left[0] == right[0] and SequenceMatcher(None, left, right).ratio() >= 0.55:
                phonetic = True
        if score >= 0.60 or phonetic:
            matches.append((hwnd, app, title, max(score, 0.60 if phonetic else score)))
    return matches


OWN_NAMES = {"your", "yours", "yourself", "you", "kova", "max", "mira", "dashboard", "kova dashboard",
             "your own", "the kova"}


def find(name, title=""):
    """The window name means: (hwnd, app, title), or (None, None, Result) explaining why not.

    An app in apps.json is found by its processes; anything else by the program or the words
    in a window's title ("minimise lofi music"). With several windows the front one is used,
    unless title picks another.
    """
    said = re.sub(r"\s+window$", "", (name or "").lower().strip())
    if said in OWN_NAMES:
        # 7 Nov log: "minimise your window" failed; it means Max's own dashboard
        found = [(hwnd, text) for hwnd, _, text in visible_windows() if _ours(hwnd)]
        if not found:
            return None, None, Result(False, "My window isn't open. Start me with --ui to get it.")
        return found[0][0], "KOVA", found[0][1]
    key, entry = system_control._resolve_app(name)
    if key and entry:
        said_name = re.sub(r"\s+window$", "", (name or "").lower().strip())
        exact_names = {key.lower(), *(alias.lower() for alias in entry.get("aliases", []))}
        if said_name not in exact_names:
            # Approximate speech matches must be resolved against open windows only,
            # not an installed-app list, so two open matches can be reported safely.
            key, entry = None, None
    if key == "file explorer":
        # 7 Nov log: Explorer windows are titled by their folder, so "file explorer" never matched
        app = key
        found = [(hwnd, text) for hwnd, process, text in visible_windows() if process.lower() == "explorer.exe"]
    elif key and entry.get("process"):
        app = key
        found = system_control.app_windows(entry["process"])
    else:
        words = system_control.title_words(name)
        matches = [(hwnd, process, text) for hwnd, process, text in visible_windows()
                   if system_control.title_has(f"{process.removesuffix('.exe')} {text}", words)]
        if not matches and len(words) > 1:
            # "open code" is OpenCode: names written as one word are said as two
            joined = "".join(words)
            matches = [(hwnd, process, text) for hwnd, process, text in visible_windows()
                       if joined in re.sub(r"\W", "", f"{process} {text}".lower())]
        found = [(hwnd, text) for hwnd, _, text in matches]
        # Found by its program ("wps photos" -> wpsphotos.exe): named after it. Found by its title
        # ("lofi music"): named as it was said, without a trailing "window"
        process = matches[0][1] if matches else ""
        by_program = process and system_control.title_has(process.removesuffix(".exe"), words)
        app = app_name(process) if by_program else re.sub(r"\s+window$", "", name)
    if title:
        wanted = system_control.title_words(title)
        found = [(hwnd, text) for hwnd, text in found if system_control.title_has(text, wanted)]
    if not found:
        near = _near_windows(name)
        if len(near) == 1:
            hwnd, app, text, _ = near[0]
            return hwnd, app, text
        if len(near) > 1:
            labels = []
            for _, app, title, _ in near[:5]:
                label = f"{app.capitalize()} — {system_control.short_title(title)}"
                if label not in labels:
                    labels.append(label)
            options = tuple(labels)
            return None, None, Result(False, f"I heard {name}. Which window did you mean: "
                                               + "; ".join(labels) + "?",
                                      halt=True, choices=options)
        shown = f" showing {title}" if title else ""
        return None, None, Result(False, f"I can't see a {app} window{shown}.")
    hwnd, text = found[0]
    return hwnd, app, text


def target_window():
    """The window you are looking at: (hwnd, app, title) of the front window that isn't the
    terminal Max runs in (in text mode that terminal is in front while you type), or None."""
    own = _own_windows()
    for hwnd, process, title in visible_windows():
        if hwnd not in own and not _is_minimised(hwnd) and not _ours(hwnd):
            # A dialog it opened (Save As, "Do you want to save?") is owned, so it isn't in the
            # list; while it is up, it is what you're looking at
            popup = _active_popup(hwnd)
            if popup:
                return popup, app_name(process), system_control._window_title(popup) or title
            return hwnd, app_name(process), title
    return None


def open_windows():
    windows = visible_windows()
    if not windows:
        return Result(True, "I can't see any open windows.")
    said = [f"{app_name(process)}, {system_control.spoken_title(title)}" for _, process, title in windows]
    message = f"{len(said)} window{'s are' if len(said) > 1 else ' is'} open: " + "; ".join(said[:MAX_LISTED])
    if len(said) > MAX_LISTED:
        message += f"; and {len(said) - MAX_LISTED} more"
    detail = "\n".join(f"  {app_name(p):<16} {t}" for _, p, t in windows)
    return Result(True, message + ".", detail=detail)


# ---------- moving windows ----------

def _save(hwnd):
    """Where the window is now: its placement, plus its real rectangle on screen."""
    return hwnd, _get_placement(hwnd), _get_rect(hwnd)


def _restore_saved(hwnd, placement, rect):
    if not _set_placement(hwnd, placement):
        return False
    normal = _normal_rect(placement)
    if normal is not None and normal != rect:
        # Snapped by Windows itself (Win+Left): the placement only knows the floating size
        # from before that, so finish with the rectangle the window really had
        return _set_pos(hwnd, rect)
    return True


def _put_back(saved):
    """Undo for one or more moves: each window goes back to where it was, even if one of them
    can't (a closed window must not stop the others)."""
    def undo():
        ok = all([_restore_saved(*window) for window in saved])
        return Result(ok, "Put back where it was." if ok else "I couldn't put it all back; a window may be closed.")
    return undo


def _admin_answer(hwnd, app, fallback):
    """After a failed move: the admin message unless the window is known not to be admin."""
    return Result(False, ADMIN.format(app) if _runs_as_admin(hwnd) is not False else fallback)


def _snap(hwnd, side, work=None):
    """Restore first (a maximised or minimised window ignores a new size), then fill the half."""
    if _is_maximised(hwnd) or _is_minimised(hwnd):
        _show(hwnd, SW_RESTORE)
    return _set_pos(hwnd, half(work or _work_area(hwnd), side, _frame_border(hwnd)))


def move(name, position, title=""):
    hwnd, app, text = find(name, title)
    if hwnd is None:
        return text  # the Result explaining why
    if position == "front":
        if system_control._bring_to_front(hwnd):
            return Result(True, f"{app.capitalize()} is in front.")
        return Result(False, f"Windows didn't let me bring {app} to the front. Click it on the taskbar.")
    # Known admin: say so at once. Unknown (Windows hid it, e.g. anti-cheat): try, and explain if it fails
    if _runs_as_admin(hwnd) is True:
        return Result(False, ADMIN.format(app))

    before = _save(hwnd)
    if position in SIDES:
        moved = _snap(hwnd, position)
        message = f"{app.capitalize()} is on the {position}."
    else:
        moved = _show(hwnd, {"maximise": SW_MAXIMIZE, "minimise": SW_MINIMIZE, "restore": SW_RESTORE}[position])
        message = {"maximise": f"Maximised {app}.", "minimise": f"Minimised {app}.",
                   "restore": f"Restored {app}."}[position]
    entry = system_control.load_apps().get("apps", {}).get(app.lower(), {})
    aliases = {app.lower(), *(alias.lower() for alias in entry.get("aliases", []))}
    heard = re.sub(r"\s+window$", "", (name or "").lower().strip())
    if heard and heard not in aliases:
        message = re.sub(rf"\b{re.escape(app)}\b", app.capitalize(), message, count=1, flags=re.I)
        message = message.rstrip(".") + f" (I heard {heard})."
    if not moved:
        return _admin_answer(hwnd, app, f"Windows didn't let me move {app}.")
    log.info("Window %s (%s) -> %s", app, text, position)
    result = Result(True, message)
    result.undo = _put_back([before])
    result.undo_text = f"put {app} back"
    return result


def side_by_side(left, right):
    """Left app on the left half and right app on the right half of the left app's screen."""
    first, first_app, problem = find(left)
    if first is None:
        return problem
    second, second_app, problem = find(right)
    if second is None:
        return problem
    if first == second:
        return Result(False, f"{left} and {right} are the same window.")
    for hwnd, app in ((first, first_app), (second, second_app)):
        if _runs_as_admin(hwnd) is True:
            return Result(False, ADMIN.format(app))

    saved = [_save(first), _save(second)]
    work = _work_area(first)
    for hwnd, app, side in ((first, first_app, "left"), (second, second_app, "right")):
        if not _snap(hwnd, side, work):
            _put_back(saved)()
            return _admin_answer(hwnd, app, f"Windows didn't let me arrange {first_app} and {second_app}.")
    log.info("Side by side: %s | %s", first_app, second_app)
    result = Result(True, f"{first_app.capitalize()} is on the left and {second_app} on the right.")
    result.undo = _put_back(saved)
    result.undo_text = f"put {first_app} and {second_app} back"
    return result


def close_window(name, wait_seconds=3):
    """Click X on one window found by its program or title ("wps photos", "pic.png"), for apps
    that aren't in apps.json. Never a terminal (that would end what runs in it) or Max's own."""
    hwnd, app, title = find(name)
    if hwnd is None:
        return title  # the Result explaining why
    return close_hwnd(hwnd, app, title, wait_seconds)


def process_of(hwnd):
    """The program of an open window ('chrome.exe'), lowercased, or ''."""
    return next((process.lower() for h, process, _ in visible_windows() if h == hwnd), "")


def close_hwnd(hwnd, app, title, wait_seconds=3):
    """Click X on this window; never a terminal or Max's own."""
    from modules import ui_automation
    if hwnd in _own_windows() or ui_automation._is_terminal(hwnd):
        return Result(False, "That's a terminal window. Say close terminal, and I'll close the others safely.")
    system_control._post_close([hwnd])
    shown = system_control.spoken_title(title)
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if system_control._window_title(hwnd) is None:
            log.info("Closed window %s (%s)", app, title)
            return Result(True, f"Closed the {app} window, {shown}.")
        time.sleep(0.2)
    return Result(False, f"The {app} window {shown} is still open. It may be asking you to save something.")


def show_desktop():
    _press_win_d()
    result = Result(True, "Showing the desktop.")
    result.undo = lambda: _press_win_d() or Result(True, "Your windows are back.")
    result.undo_text = "bring the windows back"
    return result


# ---------- DPI ----------

def dpi_aware():
    """Per-monitor DPI awareness (v2), called once at startup. Without it Windows hands Max
    scaled coordinates on a 125% or 150% screen and snapped windows land in the wrong place."""
    if os.name != "nt":
        return False
    import ctypes
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return True
    except (AttributeError, OSError):
        pass
    try:
        return ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0  # Windows 8.1 style
    except (AttributeError, OSError):
        return False


# ---------- Win32 calls (replaced in tests) ----------

def _dlls():
    """Private copies of user32/kernel32/advapi32, so the argument types set here
    don't change how the rest of Max calls them."""
    import ctypes
    from ctypes import wintypes
    if "user32" not in _DLL:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.MonitorFromWindow.restype = wintypes.HANDLE
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.GetWindow.restype = ctypes.c_void_p
        user32.GetAncestor.restype = ctypes.c_void_p
        user32.GetLastActivePopup.restype = ctypes.c_void_p
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetConsoleWindow.restype = ctypes.c_void_p
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        _DLL.update(user32=user32, kernel32=kernel32, advapi32=advapi32)
    return _DLL["user32"], _DLL["kernel32"], _DLL["advapi32"]


_DLL = {}


def _h(hwnd):
    """A window handle as a full pointer-size value (a plain int would be cut to 32 bits)."""
    from ctypes import wintypes
    return wintypes.HWND(hwnd)


def _rect_type():
    from ctypes import wintypes
    return wintypes.RECT


def _placement_type():
    import ctypes
    from ctypes import wintypes

    class WINDOWPLACEMENT(ctypes.Structure):
        _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT), ("showCmd", wintypes.UINT),
                    ("ptMinPosition", wintypes.POINT), ("ptMaxPosition", wintypes.POINT),
                    ("rcNormalPosition", wintypes.RECT)]
    return WINDOWPLACEMENT


def visible_windows():
    """The windows a person would call open, front first, as [(hwnd, process, title)]:
    visible, titled, not cloaked, not a tool window, not owned by another window."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes
    import psutil

    user32 = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    GW_OWNER, GWL_EXSTYLE, WS_EX_TOOLWINDOW, DWMWA_CLOAKED = 4, -20, 0x80, 14
    found = []

    def visit(hwnd, _):
        length = user32.GetWindowTextLengthW(hwnd)
        if not (length and user32.IsWindowVisible(hwnd)) or user32.GetWindow(hwnd, GW_OWNER):
            return True
        if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        state = wintypes.DWORD()
        if not ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(state),
                                                          ctypes.sizeof(state)) and state.value:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        if title.value == "Program Manager":  # the desktop itself
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            process = psutil.Process(pid.value).name().lower()
        except (psutil.Error, ValueError):
            process = ""
        found.append((hwnd, process, title.value))
        return True

    user32.EnumWindows(callback_type(visit), 0)
    return found


def _get_placement(hwnd):
    import ctypes
    user32, _, _ = _dlls()
    placement = _placement_type()()
    placement.length = ctypes.sizeof(placement)
    user32.GetWindowPlacement(_h(hwnd), ctypes.byref(placement))
    return placement


def _set_placement(hwnd, placement):
    import ctypes
    user32, _, _ = _dlls()
    return bool(user32.SetWindowPlacement(_h(hwnd), ctypes.byref(placement)))


def _get_rect(hwnd):
    """The window's real rectangle on screen as (x, y, width, height)."""
    import ctypes
    rect = _rect_type()()
    _dlls()[0].GetWindowRect(_h(hwnd), ctypes.byref(rect))
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def _normal_rect(placement):
    """The floating rectangle a normal (not minimised or maximised) window will get back, else None."""
    SW_SHOWNORMAL = 1
    if placement.showCmd != SW_SHOWNORMAL:
        return None
    r = placement.rcNormalPosition
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def _is_maximised(hwnd):
    return bool(_dlls()[0].IsZoomed(_h(hwnd)))


def _is_minimised(hwnd):
    return bool(_dlls()[0].IsIconic(_h(hwnd)))


def _show(hwnd, command):
    _dlls()[0].ShowWindow(_h(hwnd), command)
    return True  # ShowWindow returns whether it *was* visible, not whether it worked


def _set_pos(hwnd, rect):
    SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x0010, 0x0040
    x, y, width, height = rect
    # HWND_TOP (0): the moved window comes above the others, so you see where it went
    return bool(_dlls()[0].SetWindowPos(hwnd, 0, x, y, width, height, SWP_NOACTIVATE | SWP_SHOWWINDOW))


def _monitor_info(hwnd):
    """MONITORINFO of the screen the window is on (hwnd None: the main screen)."""
    import ctypes
    from ctypes import wintypes

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    user32, _, _ = _dlls()
    MONITOR_DEFAULTTOPRIMARY, MONITOR_DEFAULTTONEAREST = 1, 2
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(info)
    monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST if hwnd else MONITOR_DEFAULTTOPRIMARY)
    user32.GetMonitorInfoW(monitor, ctypes.byref(info))
    return info


def _work_area(hwnd):
    """The screen the window is on, minus the taskbar, as (left, top, right, bottom)."""
    r = _monitor_info(hwnd).rcWork
    return r.left, r.top, r.right, r.bottom


def _monitor_rect(hwnd):
    """The whole screen the window is on (hwnd None: the main screen) as (left, top, right, bottom)."""
    r = _monitor_info(hwnd).rcMonitor
    return r.left, r.top, r.right, r.bottom


def _frame_border(hwnd):
    """The invisible frame around the window as (left, top, right, bottom) pixels."""
    import ctypes
    user32, _, _ = _dlls()
    rect, frame = _rect_type()(), _rect_type()()
    DWMWA_EXTENDED_FRAME_BOUNDS = 9
    user32.GetWindowRect(_h(hwnd), ctypes.byref(rect))
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(_h(hwnd), DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(frame),
                                                  ctypes.sizeof(frame)):
        return 0, 0, 0, 0
    return (max(0, frame.left - rect.left), max(0, frame.top - rect.top),
            max(0, rect.right - frame.right), max(0, rect.bottom - frame.bottom))


def _process_elevated(pid):
    """True if the process runs as admin, False if not, None when Windows won't say
    (a protected process, e.g. a game's anti-cheat, can hide it without being admin)."""
    import ctypes
    from ctypes import wintypes
    _, kernel32, advapi32 = _dlls()
    PROCESS_QUERY_LIMITED_INFORMATION, TOKEN_QUERY, TokenElevation = 0x1000, 0x0008, 20
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(handle, TOKEN_QUERY, ctypes.byref(token)):
            return None
        try:
            elevated, size = wintypes.DWORD(), wintypes.DWORD()
            ok = advapi32.GetTokenInformation(token, TokenElevation, ctypes.byref(elevated),
                                              ctypes.sizeof(elevated), ctypes.byref(size))
            return bool(elevated.value) if ok else None
        finally:
            kernel32.CloseHandle(token)
    finally:
        kernel32.CloseHandle(handle)


def _runs_as_admin(hwnd):
    """True if the window belongs to an admin program while Max isn't admin: Windows then
    ignores Max's moves (UIPI), so Max says why. False if it's within reach, None if unknown."""
    import ctypes
    from ctypes import wintypes
    user32, _, _ = _dlls()
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(_h(hwnd), ctypes.byref(pid))
    if not pid.value or _process_elevated(os.getpid()):
        return False  # Max running as admin can move anything
    return _process_elevated(pid.value)


def _own_windows():
    """Max's own terminal window(s). Classic consoles: the console window itself. Windows Terminal:
    the console is hidden and its owner is the Terminal window you see."""
    if os.name != "nt":
        return set()
    user32, kernel32, _ = _dlls()
    console = kernel32.GetConsoleWindow()
    if not console:
        return set()
    GW_OWNER, GA_ROOTOWNER = 4, 3
    handle = _h(console)
    return {h for h in (console, user32.GetWindow(handle, GW_OWNER), user32.GetAncestor(handle, GA_ROOTOWNER)) if h}


def _ours(hwnd):
    """True for a window of this very process: the dashboard (--ui) is in front while you type,
    but "save this file" means the window behind it."""
    if os.name != "nt":
        return False
    import ctypes
    user32, _, _ = _dlls()
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(_h(hwnd), ctypes.byref(pid))
    return pid.value == os.getpid()


def _active_popup(hwnd):
    """The visible dialog this window has open (its enabled popup), or None."""
    user32, _, _ = _dlls()
    GW_ENABLEDPOPUP = 6
    handle = _h(hwnd)
    for popup in (user32.GetWindow(handle, GW_ENABLEDPOPUP), user32.GetLastActivePopup(handle)):
        if popup and popup != hwnd and user32.IsWindowVisible(_h(popup)) and not _is_tiny(popup):
            return popup
    return None


def _is_tiny(hwnd):
    """A tooltip, tab hover card or info bar, not a dialog (9 Nov: Edge's 153x25 hover card was
    taken for the window in front, so "click Run speed test" looked in it and found nothing)."""
    import ctypes
    from ctypes import wintypes
    rect = wintypes.RECT()
    if not ctypes.windll.user32.GetWindowRect(_h(hwnd), ctypes.byref(rect)):
        return False
    return rect.right - rect.left < 200 or rect.bottom - rect.top < 80


def _press_win_d():
    import ctypes
    user32 = ctypes.windll.user32
    VK_LWIN, VK_D, KEYUP = 0x5B, 0x44, 0x0002
    user32.keybd_event(VK_LWIN, 0, 0, 0)
    user32.keybd_event(VK_D, 0, 0, 0)
    user32.keybd_event(VK_D, 0, KEYUP, 0)
    user32.keybd_event(VK_LWIN, 0, KEYUP, 0)
