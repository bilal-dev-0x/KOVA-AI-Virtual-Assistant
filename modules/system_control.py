import json
import os
import re
import subprocess
import time
import webbrowser

from modules.fuzzy import best_match
from modules.logger import get_logger
from modules.result import Result

APPS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "apps.json")

log = get_logger()
_apps_cache = None
_start_menu_cache = None


def load_apps(path=APPS_FILE):
    global _apps_cache
    if _apps_cache is None:
        with open(path, "r", encoding="utf-8") as f:
            _apps_cache = json.load(f)
    return _apps_cache


def start_menu_apps():
    """Every app in the Windows Start menu as {name lowercase: (name, AppID)}.

    Asked once per run (about half a second), so any installed app can be
    opened without adding it to apps.json first.
    """
    global _start_menu_cache
    if _start_menu_cache is None:
        _start_menu_cache = {}
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "[Console]::OutputEncoding = [Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress"],
                capture_output=True, text=True, encoding="utf-8", timeout=15
            )
            for app in json.loads(out.stdout or "[]"):
                # Skip web links such as "Git FAQs" that point to a website
                if app.get("Name") and app.get("AppID") and not app["AppID"].startswith("http"):
                    _start_menu_cache[app["Name"].lower()] = (app["Name"], app["AppID"])
        except Exception as e:
            log.error("Could not read Start menu apps: %s", e)
        log.info("Found %s Start menu apps", len(_start_menu_cache))
    return _start_menu_cache


def _start_menu_target(key):
    name, app_id = start_menu_apps()[key]
    return ("app", name, {"launch": {"appid": app_id}, "process": _guess_process(name), "start_menu": True})


def _guess_process(app_name):
    """The running process of a Start menu app, found by its name:
    'Epic Games Launcher' -> ['EpicGamesLauncher.exe']. Empty if none is sure enough."""
    import psutil
    wanted = re.sub(r"[^a-z0-9]", "", app_name.lower())
    if len(wanted) < 4:
        return []
    for proc in psutil.process_iter(["name"]):
        name = proc.info["name"] or ""
        if name.lower() in PROTECTED_PROCESSES:
            continue
        compact = re.sub(r"[^a-z0-9]", "", name.lower().removesuffix(".exe"))
        shorter = min(len(compact), len(wanted))
        if compact == wanted or (shorter >= 6 and (compact.startswith(wanted) or wanted.startswith(compact))):
            return [name]
    return []


def is_app_alias(name):
    """True if name is exactly an app's name or alias in apps.json ("google chrome")."""
    name = name.lower().strip()
    return any(name == key or name in entry.get("aliases", []) for key, entry in load_apps().get("apps", {}).items())


def resolve(name):
    """Find what the user means by name.

    Returns ("app", key, entry), ("website", key, url) or None.
    Order: exact name or alias in apps.json, exact Start menu name,
    then fuzzy matches in the same order.
    """
    data = load_apps()
    apps = data.get("apps", {})
    sites = data.get("websites", {})
    name = name.lower().strip()

    lookup = {}
    for key, entry in apps.items():
        lookup[key] = ("app", key)
        for alias in entry.get("aliases", []):
            lookup[alias] = ("app", key)
    for key in sites:
        lookup.setdefault(key, ("website", key))

    def found(match):
        kind, key = lookup[match]
        return (kind, key, apps[key] if kind == "app" else sites[key])

    if name in lookup:
        return found(name)

    start_menu = start_menu_apps()
    if name in start_menu:
        return _start_menu_target(name)

    match = best_match(name, lookup.keys(), cutoff=0.75)
    if match:
        return found(match)

    match = best_match(name, start_menu.keys(), cutoff=0.8)
    if match:
        return _start_menu_target(match)

    return None


def check_app(name, must_be_running=False):
    """Make sure name is an app KOVA can close or restart.

    Returns (app_key, None) when it's fine, or (None, Result) explaining why not.
    """
    target = resolve(name)
    if target is None:
        return None, Result(False, f"I don't know an app called {name}.")

    kind, key, entry = target
    if kind == "website":
        return None, Result(False, f"{key} is a website. I can't close browser tabs yet.")
    if entry.get("start_menu") and not entry.get("process"):
        if must_be_running:
            return None, Result(False, f"I can't find {key} running, so I can't close it. "
                                       "If it is open, add it to apps.json with its process name.")
        return None, Result(False, f"I can open {key}, but I don't know how to close it yet. "
                                   "Add it to apps.json with its process name.")
    if not entry.get("process"):
        return None, Result(False, f"I'm not allowed to close {key}.")
    if must_be_running and not _is_running(entry):
        return None, Result(True, f"{key} is not running.")
    if must_be_running and _needs_admin(entry["process"]):
        # Say so at once instead of clicking X for 5 seconds (Task Manager)
        return None, Result(False, ADMIN_MESSAGE.format(key))
    return key, None


def open_url(url, config=None):
    browser = (config or {}).get("BROWSER", "default")
    if browser and browser != "default" and os.path.exists(browser):
        subprocess.Popen([browser, url])
    else:
        webbrowser.open(url)


def _launch(entry):
    launch = entry["launch"]
    if "appid" in launch:
        # shell:AppsFolder can start anything from the Start menu, including Store apps
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{launch['appid']}"])
    elif "uri" in launch:
        os.startfile(launch["uri"])
    else:
        exe = os.path.expandvars(launch["exe"])
        try:
            subprocess.Popen([exe])
        except OSError as e:
            if getattr(e, "winerror", None) != 740:
                raise
            # Error 740: the app needs admin rights (Task Manager). Opening it through the
            # shell shows Windows' own admin prompt, which you answer yourself
            os.startfile(exe)


# "discord com", "discord in browser", "discord website" mean the website, not the app
WEB_SUFFIX = re.compile(r" (dot com|com|in (the )?(browser|chrome)|on the web|website|site)$")


def website_for(name):
    """The website (key, url) for 'discord com' or 'discord in browser', or None."""
    name = name.lower().strip()
    if not WEB_SUFFIX.search(name):
        return None
    base = name
    while WEB_SUFFIX.search(base):  # "discord com in browser"
        base = WEB_SUFFIX.sub("", base).strip()
    if not base:
        return None
    sites = load_apps().get("websites", {})
    if base in sites:
        return base, sites[base]
    return base, f"https://www.{base.replace(' ', '')}.com/"


BROWSER_PROCESSES = {"chrome.exe", "brave.exe", "msedge.exe", "firefox.exe"}
APP_PATHS = "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\"


def browser_exe(process_name):
    """Full path of an installed browser from Windows' App Paths, or None."""
    if os.name != "nt":
        return None
    import winreg
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            return winreg.QueryValue(root, APP_PATHS + process_name)
        except OSError:
            continue
    return None


def _browser(name):
    """(key, exe) when name is a browser Max can start with arguments, else (key, None)."""
    key, entry = _resolve_app(name)
    if key is None:
        return None, None
    process = next((p.lower() for p in entry.get("process", []) if p.lower() in BROWSER_PROCESSES), None)
    return key, browser_exe(process) if process else None


def open_in_browser(name, url):
    """Open url in the browser the user named ("search cats in brave")."""
    key, exe = _browser(name)
    if not exe:
        open_url(url)
        return Result(True, f"I can't pick {name}, so I opened it in your default browser")
    subprocess.Popen([exe, url])
    return Result(True, f"Opening it in {key}")


def new_window(name, config=None):
    """Another window of an app. Browsers get a real new window; other apps are opened again."""
    key, exe = _browser(name)
    if key is None:
        return Result(False, f"I don't know an app called {name}.")
    if exe:
        flag = "-new-window" if exe.lower().endswith("firefox.exe") else "--new-window"
        subprocess.Popen([exe, flag])
        return Result(True, f"Opening a new {key} window")
    result = open_target(key, config)
    if result.success:
        result.message = f"Opening {key} again. Some apps only bring their window to the front."
    return result


def open_target(name, config=None):
    website = website_for(name)
    if website:
        open_url(website[1], config)
        return Result(True, f"Opening {website[0]} in the browser")

    target = resolve(name)
    if target is None:
        return Result(False, f"I don't know an app or website called {name}.")

    kind, key, value = target
    try:
        if kind == "website":
            open_url(value, config)
        else:
            _launch(value)
    except Exception as e:
        log.error("Failed to open %s: %s", key, e)
        return Result(False, f"I couldn't open {key}.", detail=str(e))

    log.info("Opened %s %s", kind, key)
    return Result(True, f"Opening {key}")


def _running_names():
    import psutil
    names = set()
    for proc in psutil.process_iter(["name"]):
        if proc.info["name"]:
            names.add(proc.info["name"].lower())
    return names


def _is_running(entry, names=None):
    names = names if names is not None else _running_names()
    return any(p.lower() in names for p in entry.get("process", []))


def running_apps():
    names = _running_names()
    running = [key for key, entry in load_apps().get("apps", {}).items() if _is_running(entry, names)]

    if not running:
        return Result(True, "None of your configured apps are running.")
    return Result(True, f"Running apps: {', '.join(running)}.")


def _resolve_app(name):
    target = resolve(name)
    if target is None or target[0] != "app":
        return None, None
    return target[1], target[2]


def app_windows(proc_names):
    """The visible windows of these processes as [(handle, title)], in screen order.

    Windows Store apps (Calculator, Settings, Photos) count too: their window belongs to
    ApplicationFrameHost.exe, with the app's own window inside it.
    """
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes
    import psutil

    user32 = ctypes.windll.user32
    wanted = {p.lower() for p in proc_names}
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def owner(hwnd):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            return psutil.Process(pid.value).name().lower()
        except (psutil.Error, ValueError):
            return ""

    def hosts_wanted_app(frame):
        found = []
        def check(child, _):
            if owner(child) in wanted:
                found.append(child)
                return False
            return True
        user32.EnumChildWindows(frame, callback_type(check), 0)
        return bool(found)

    windows = []
    def visit(hwnd, _):
        length = user32.GetWindowTextLengthW(hwnd)
        if user32.IsWindowVisible(hwnd) and length:
            name = owner(hwnd)
            if name in wanted or (name == "applicationframehost.exe" and hosts_wanted_app(hwnd)):
                title = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, title, length + 1)
                windows.append((hwnd, title.value))
        return True

    user32.EnumWindows(callback_type(visit), 0)
    return windows


def _post_close(hwnds):
    """Click X on these windows (WM_CLOSE), so each can still ask to save."""
    import ctypes
    for hwnd in hwnds:
        ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)


def short_title(title):
    """'YouTube - Google Chrome' -> 'YouTube', short enough to say out loud."""
    if " - " in title:
        title = title.rsplit(" - ", 1)[0]
    return title[:60].strip()


def close_app(name, window="", wait_seconds=5):
    """Close an app gently, or only its window whose title contains window."""
    key, entry = _resolve_app(name)
    if key is None:
        return Result(False, f"I don't know an app called {name}.")
    if not entry.get("process"):
        return Result(False, f"I'm not allowed to close {key}.")
    if not _is_running(entry):
        return Result(True, f"{key} is not running.")
    procs = entry["process"]

    if window:
        chosen = {hwnd for hwnd, title in app_windows(procs) if window.lower() in title.lower()}
        if not chosen:
            return Result(False, f"{key} has no window called {window}.")
        _post_close(chosen)
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            if not chosen & {hwnd for hwnd, _ in app_windows(procs)}:
                log.info("Closed %s window %s", key, window)
                return Result(True, f"Closed the {key} window {window}")
            time.sleep(0.5)
        return Result(False, f"That {key} window is still open. It may be asking you to save something.")

    had_windows = bool(app_windows(procs))
    # taskkill without /F asks the app to close normally, like clicking X,
    # so it can still ask you to save your work
    for proc_name in procs:
        subprocess.run(["taskkill", "/IM", proc_name], capture_output=True)

    deadline = time.time() + wait_seconds
    next_click = time.time() + 1.5
    while time.time() < deadline:
        if not _is_running(entry):
            log.info("Closed %s", key)
            return Result(True, f"Closed {key}")
        remaining = app_windows(procs)
        if had_windows and not remaining:
            # Every window is gone; Chrome and others may keep running in the background
            time.sleep(1)
            if not _is_running(entry):
                return Result(True, f"Closed {key}")
            log.info("Closed %s's windows, it still runs in the background", key)
            return Result(True, f"Closed {key}. It's still running in the background; "
                                f"say force close {key} to end that too.")
        if remaining and time.time() >= next_click:
            # taskkill missed these (Store apps, or a second Chrome window): click X on each,
            # again every second until they are gone
            _post_close(hwnd for hwnd, _ in remaining)
            log.info("Asked %s window(s) of %s to close", len(remaining), key)
            next_click = time.time() + 1
        time.sleep(0.5)

    log.info("%s did not close within %ss", key, wait_seconds)
    if _needs_admin(procs):
        return Result(False, ADMIN_MESSAGE.format(key))
    return Result(False, f"{key} is still open. It may be asking you to save something. "
                         f"If it's stuck, say force close {key}.")


ADMIN_MESSAGE = ("{} is running with administrator rights, so Windows doesn't let me close it, "
                 "not even by force. Please close it yourself.")


# These always run as administrator on an admin account, however they were started
ALWAYS_ADMIN = {"taskmgr.exe", "regedit.exe", "mmc.exe"}


def _needs_admin(proc_names):
    """True if a running process of these is out of Max's reach (it runs as administrator).

    Opening the process to end it can succeed even for an admin process, so its owner is
    checked too: Windows hides the owner of admin and system processes from normal programs.
    """
    if os.name != "nt":
        return False
    import ctypes
    import psutil

    wanted = {p.lower() for p in proc_names}
    PROCESS_TERMINATE = 0x0001
    kernel32 = ctypes.windll.kernel32
    for proc in psutil.process_iter(["name"]):
        name = (proc.info["name"] or "").lower()
        if name not in wanted:
            continue
        if name in ALWAYS_ADMIN:
            return True
        try:
            proc.username()
        except psutil.AccessDenied:
            return True
        except psutil.Error:
            continue
        handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, proc.pid)
        if not handle:
            return True
        kernel32.CloseHandle(handle)
    return False


def app_memory_mb(name):
    """RAM used by all processes of an app in apps.json, in MB (0 if unknown or not running)."""
    import psutil
    key, entry = _resolve_app(name)
    if key is None:
        return 0
    wanted = {p.lower() for p in entry.get("process", [])}
    total = 0
    for proc in psutil.process_iter(["name", "memory_info"]):
        if (proc.info["name"] or "").lower() in wanted and proc.info["memory_info"]:
            total += proc.info["memory_info"].rss
    return total / 1024 ** 2


# Windows needs these to keep running, and Max needs itself
PROTECTED_PROCESSES = {
    "system", "system idle process", "registry", "memory compression", "memcompression", "smss.exe", "csrss.exe",
    "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe", "svchost.exe", "dwm.exe",
    "explorer.exe", "fontdrvhost.exe", "sihost.exe", "taskhostw.exe", "ctfmon.exe", "conhost.exe",
    "audiodg.exe", "spoolsv.exe", "msmpeng.exe", "securityhealthservice.exe", "searchindexer.exe",
    "python.exe", "pythonw.exe", "py.exe", "powershell.exe", "windowsterminal.exe", "openconsole.exe",
}


def _processes_named(process_name):
    import psutil
    wanted = process_name.lower()
    return [p for p in psutil.process_iter(["name", "exe"]) if (p.info["name"] or "").lower() == wanted]


def check_process(process_name):
    """Returns a Result explaining why process_name can't be ended, or None if it can."""
    name = process_name.lower()
    if not name.endswith(".exe") and name not in PROTECTED_PROCESSES:
        name += ".exe"
    if name in PROTECTED_PROCESSES:
        return Result(False, f"{process_name} is part of Windows or of me, so I won't end it.")
    if not _processes_named(name):
        return Result(False, f"No process called {name} is running.")
    return None


def end_process(process_name, wait_seconds=5):
    """Ask every process with this name to close, the same gentle way as close_app.

    Returns a Result whose undo starts the program again.
    """
    name = process_name.lower()
    if not name.endswith(".exe"):
        name += ".exe"
    problem = check_process(name)
    if problem:
        return problem

    procs = _processes_named(name)
    exe = next((p.info["exe"] for p in procs if p.info["exe"]), None)
    subprocess.run(["taskkill", "/IM", name], capture_output=True)

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if not _processes_named(name):
            log.info("Ended process %s", name)
            result = Result(True, f"Ended {name}")
            if exe:
                result.undo = lambda: _relaunch(exe)
                result.undo_text = f"start {name} again"
            return result
        time.sleep(0.5)

    log.info("%s did not end within %ss", name, wait_seconds)
    return Result(False, f"{name} didn't close. It may be busy. "
                         f"If it's stuck, I can force close it, but unsaved work in it is lost.")


def _force_targets(name):
    """The app key (or process name) and the processes a force close would end."""
    key, entry = _resolve_app(name)
    if key is not None and entry.get("process"):
        return key, [p.lower() for p in entry["process"]]
    process = name.lower() if name.lower().endswith(".exe") else name.lower() + ".exe"
    return process, [process]


def check_force(name):
    """Returns (target, None) if name can be force closed, or (None, Result) why not."""
    key, entry = _resolve_app(name)
    if key is not None and not entry.get("process"):
        return None, Result(False, f"I'm not allowed to close {key}.")
    target, processes = _force_targets(name)
    for process in processes:
        if process in PROTECTED_PROCESSES:
            return None, Result(False, f"{target} is part of Windows or of me, so I won't force it closed.")
    if not any(_processes_named(p) for p in processes):
        return None, Result(False, f"{target} is not running.")
    if _needs_admin(processes):
        return None, Result(False, ADMIN_MESSAGE.format(target))
    return target, None


def force_close(name, wait_seconds=3):
    """End an app at once (taskkill /F). Unsaved work in it is lost, so it's always asked first."""
    target, problem = check_force(name)
    if problem:
        return problem
    _, processes = _force_targets(target)
    for process in processes:
        subprocess.run(["taskkill", "/F", "/IM", process], capture_output=True)

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if not any(_processes_named(p) for p in processes):
            log.info("Force closed %s", target)
            return Result(True, f"Force closed {target}")
        time.sleep(0.3)
    if _needs_admin(processes):
        return Result(False, ADMIN_MESSAGE.format(target))
    return Result(False, f"{target} is still running even after forcing. Windows may need admin rights for it.")


def _relaunch(exe):
    subprocess.Popen([exe])
    return Result(True, f"Started {os.path.basename(exe)} again")


def restart_app(name, config=None):
    key, entry = _resolve_app(name)
    if key is None:
        return Result(False, f"I don't know an app called {name}.")

    if _is_running(entry):
        closed = close_app(key)
        if not closed.success:
            return closed
        time.sleep(1)

    opened = open_target(key, config)
    if opened.success:
        return Result(True, f"Restarted {key}")
    return opened
