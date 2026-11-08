"""Read-only, small data views for the Phase C dashboard screens.

Actions are deliberately not implemented here. The page queues them through
the Controller so the normal Agent checks, approvals, and action log apply.
"""

import json
import os
import re
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from modules import actions, files, performance_monitor as perf, system_control

APP_LIMIT = 500
FILE_LIMIT = 100
FILE_SECONDS = 3.0
ACTIVITY_LIMIT = 100


def _running_process_names():
    import psutil

    names = set()
    for process in psutil.process_iter(["name"]):
        try:
            if process.info["name"]:
                names.add(process.info["name"].lower())
        except (psutil.Error, OSError):
            continue
    return names


def _compact(value):
    return re.sub(r"[^a-z0-9]", "", (value or "").lower().removesuffix(".exe"))


def _app_is_running(name, running, known_apps):
    key = name.strip().lower()
    expected = set()
    for app_key, entry in known_apps.items():
        labels = {app_key.lower(), *(alias.lower() for alias in entry.get("aliases", []))}
        if key in labels:
            expected.update(process.lower() for process in entry.get("process", []))
    if expected & running:
        return True

    wanted = _compact(name)
    if len(wanted) < 6:
        return False
    return any((proc := _compact(process)) == wanted or
               (len(proc) >= 6 and (proc.startswith(wanted) or wanted.startswith(proc)))
               for process in running)


def applications(query=""):
    """Installed Start-menu apps with a best-effort running-process flag."""
    try:
        found = system_control.start_menu_apps()
    except Exception:
        found = {}
    try:
        running = _running_process_names()
    except Exception:
        running = set()
    try:
        known_apps = system_control.load_apps().get("apps", {})
    except Exception:
        known_apps = {}

    words = (query or "").strip().casefold()
    rows = []
    for _, (name, _) in sorted(found.items(), key=lambda pair: pair[1][0].casefold()):
        if words and words not in name.casefold():
            continue
        rows.append({"name": name, "running": _app_is_running(name, running, known_apps)})
        if len(rows) >= APP_LIMIT:
            break
    return {"items": rows, "error": None}


def _folder_roots():
    allowed = files.allowed_roots()
    allowed_norm = {os.path.normcase(os.path.realpath(path)) for path in allowed}
    roots = []
    used = set()
    for key in files.KNOWN_FOLDERS:
        path = files.known_folder(key)
        norm = os.path.normcase(os.path.realpath(path))
        if norm in allowed_norm and norm not in used and os.path.isdir(path):
            roots.append({"name": key.title(), "path": path})
            used.add(norm)
    for path in allowed:
        norm = os.path.normcase(os.path.realpath(path))
        if norm in used or not os.path.isdir(path):
            continue
        drive = Path(path).drive
        name = f"{drive} drive" if drive else Path(path).name or path
        roots.append({"name": name, "path": path})
        used.add(norm)
    return roots


def _file_row(entry):
    try:
        if entry.is_symlink() or files._hidden(entry) or entry.name.startswith("."):
            return None
        if entry.name.lower() in files.SKIP_DIRS or entry.name.lower() in files.PROTECTED_PARTS:
            return None
        stat = entry.stat(follow_symlinks=False)
        is_dir = entry.is_dir(follow_symlinks=False)
    except OSError:
        return None
    try:
        modified = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="minutes")
    except (OSError, OverflowError, ValueError):
        modified = None
    return {"name": entry.name, "path": entry.path, "is_dir": is_dir,
            "size": None if is_dir else stat.st_size, "modified": modified}


def _inside_allowed(path, roots):
    real = os.path.realpath(path)
    return any(files._inside(real, root["path"]) for root in roots)


def files_view(folder="", query=""):
    """List a safe folder or search below it, bounded by time and result count."""
    roots = _folder_roots()
    response = {"roots": roots, "folder": None, "items": [], "error": None, "truncated": False}
    if not folder:
        return response

    path = os.path.realpath(folder)
    problem = files.check_place(path, change=False)
    if problem or not _inside_allowed(path, roots) or not os.path.isdir(path):
        response["error"] = problem.message if problem else "This folder is not available in the allowed locations."
        return response
    if files._hidden_path(path):
        response["error"] = "Hidden and system folders are not shown."
        return response

    response["folder"] = path
    terms = files._query_words(query or "")
    deadline = time.monotonic() + FILE_SECONDS
    candidates = []
    stack = [path]
    while stack and len(candidates) < FILE_LIMIT:
        if time.monotonic() >= deadline:
            response["truncated"] = True
            break
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            if time.monotonic() >= deadline:
                response["truncated"] = True
                break
            row = _file_row(entry)
            if row is None:
                continue
            if not terms:
                candidates.append(row)
            elif files._matches(entry.name, terms):
                candidates.append(row)
            if terms and entry.is_dir(follow_symlinks=False):
                child = os.path.realpath(entry.path)
                if (_inside_allowed(child, roots) and not files._hidden_path(child)
                        and entry.name.lower() not in files.SKIP_DIRS):
                    stack.append(child)
            if len(candidates) >= FILE_LIMIT:
                response["truncated"] = True
                break
        if response["truncated"]:
            break

    response["items"] = sorted(candidates, key=lambda row: (not row["is_dir"], row["name"].casefold()))
    return response


def _process_rows():
    result = perf.top_processes("ram", count=8)
    rows = []
    for line in (result.detail or "").splitlines():
        try:
            name, amount, unit = line.strip().rsplit(None, 2)
            if unit.upper() == "MB":
                rows.append({"name": name, "ram_mb": float(amount)})
        except (ValueError, TypeError):
            continue
    return rows


def _percent_value(value):
    try:
        return float(str(value).split("%", 1)[0])
    except (TypeError, ValueError):
        return None


def _cached_ram(reading):
    try:
        used, total = reading["ram"].split("/", 1)[0], reading["ram"].split("/", 1)[1].split()[0]
        return {"used_gb": float(used), "total_gb": float(total),
                "percent": _percent_value(reading.get("ram_percent"))}
    except (AttributeError, IndexError, KeyError, ValueError):
        return None


def _cached_battery(value):
    percent = _percent_value(value)
    if percent is None:
        return None
    return {"percent": percent, "plugged": "charging" in str(value).lower()}


def system_status(cached=None):
    """One point-in-time real system snapshot; each unavailable figure stays null."""
    snapshot = {"cpu_percent": None, "ram": None, "disks": [], "battery": None,
                "gpu_percent": None, "processes": [], "errors": []}
    if cached is not None:
        snapshot["cpu_percent"] = _percent_value(cached.get("cpu"))
        snapshot["ram"] = _cached_ram(cached)
        snapshot["battery"] = _cached_battery(cached.get("battery"))
        snapshot["gpu_percent"] = _percent_value(cached.get("gpu"))
    else:
        try:
            snapshot["cpu_percent"] = perf.cpu_percent(interval=0.2)
        except Exception:
            snapshot["errors"].append("CPU")
        try:
            snapshot["ram"] = perf.ram_info()
        except Exception:
            snapshot["errors"].append("RAM")
        try:
            battery = perf.battery_info()
            snapshot["battery"] = battery
        except Exception:
            snapshot["errors"].append("Battery")
        try:
            overall, _ = perf.gpu_usage(perf._gpu_samples())
            snapshot["gpu_percent"] = overall
        except Exception:
            snapshot["errors"].append("GPU")
    try:
        snapshot["disks"] = perf.disk_info()
    except Exception:
        snapshot["errors"].append("Disk")
    try:
        snapshot["processes"] = _process_rows()
    except Exception:
        snapshot["errors"].append("Processes")
    return snapshot


def activity(agent=None, limit=ACTIVITY_LIMIT):
    """Recent action records, newest first, without exposing raw tool arguments."""
    limit = max(1, min(int(limit), ACTIVITY_LIMIT))
    rows = deque(maxlen=limit)
    try:
        with open(actions.ACTIONS_LOG, encoding="utf-8") as stream:
            for line in stream:
                try:
                    item = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if (not isinstance(item, dict) or not isinstance(item.get("time"), str)
                        or not isinstance(item.get("success"), bool)):
                    continue
                rows.append({key: item.get(key) for key in
                             ("time", "source", "tool", "level", "approval", "success", "message", "undo_text")})
    except OSError:
        pass

    undo_stack = getattr(getattr(agent, "runner", None), "undo_stack", ())
    latest_undo = undo_stack[-1][0] if undo_stack else None
    result = []
    for row in reversed(rows):
        row["undo_available"] = bool(row.get("undo_text") and row.get("undo_text") == latest_undo)
        result.append(row)
    return {"items": result, "error": None}
