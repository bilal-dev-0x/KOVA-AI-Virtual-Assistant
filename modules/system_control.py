import json
import os
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
    return ("app", name, {"launch": {"appid": app_id}, "process": [], "start_menu": True})


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
    if entry.get("start_menu"):
        return None, Result(False, f"I can open {key}, but I don't know how to close it yet. "
                                   "Add it to apps.json with its process name.")
    if not entry.get("process"):
        return None, Result(False, f"I'm not allowed to close {key}.")
    if must_be_running and not _is_running(entry):
        return None, Result(True, f"{key} is not running.")
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
        subprocess.Popen([os.path.expandvars(launch["exe"])])


def open_target(name, config=None):
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


def close_app(name, wait_seconds=5):
    key, entry = _resolve_app(name)
    if key is None:
        return Result(False, f"I don't know an app called {name}.")
    if not entry.get("process"):
        return Result(False, f"I'm not allowed to close {key}.")
    if not _is_running(entry):
        return Result(True, f"{key} is not running.")

    # taskkill without /F asks the app to close normally, like clicking X,
    # so it can still ask you to save your work
    for proc_name in entry["process"]:
        subprocess.run(["taskkill", "/IM", proc_name], capture_output=True)

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if not _is_running(entry):
            log.info("Closed %s", key)
            return Result(True, f"Closed {key}")
        time.sleep(0.5)

    log.info("%s did not close within %ss", key, wait_seconds)
    return Result(False, f"{key} is still open. It may be asking you to save something.")


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
