"""Small persistent schedules for user-approved, recurring KOVA tasks."""
import json
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path

from modules.logger import get_logger
from modules import tool_registry
from modules.tool_registry import BASE_DIR, CRITICAL

log = get_logger()
TASKS_FILE = Path(BASE_DIR) / "data" / "scheduled_tasks.json"
CHECK_SECONDS = 60
_lock = threading.RLock()
_timer = None
_event_timer = None
_runner = None
_notify = None


def _load():
    try:
        data = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
        rows = data.get("tasks", []) if isinstance(data, dict) else []
        clean = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or not str(row.get("id", "")).isdigit():
                continue
            task = dict(row)
            if task.get("kind") == "daily":
                action = tool_registry.get("open_app")
                args, error = action.validate(task.get("args")) if action else (None, "missing tool")
                at_time = _clock(task.get("at_time", ""))
                if task.get("tool") != "open_app" or error or not at_time:
                    continue
                task.update(at_time=at_time, args=args, last_run=str(task.get("last_run", "")))
            elif task.get("kind") == "battery_below":
                try:
                    threshold = int(task.get("threshold"))
                except (TypeError, ValueError):
                    continue
                if not 5 <= threshold <= 95:
                    continue
                task.update(threshold=threshold, armed=bool(task.get("armed", True)))
            else:
                continue
            task["id"] = int(task["id"])
            clean.append(task)
        return clean
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        log.error("Could not read scheduled tasks: %s", exc)
        return []


def _save(rows):
    TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = TASKS_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps({"tasks": rows}, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, TASKS_FILE)


def _clock(text):
    value = (text or "").strip().lower().replace(".", "")
    for pattern in ("%H:%M", "%I:%M %p", "%I:%M%p", "%I %p", "%I%p"):
        try:
            return datetime.strptime(value, pattern).strftime("%H:%M")
        except ValueError:
            continue
    if value.isdigit() and 0 <= int(value) <= 23:
        return f"{int(value):02d}:00"
    return None


def _next_delay(at_time, now=None):
    now = now or datetime.now()
    hour, minute = map(int, at_time.split(":"))
    due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if due <= now:
        due += timedelta(days=1)
    return (due - now).total_seconds()


def add_daily(at_time, app, description):
    at_time = _clock(at_time)
    app = (app or "").strip().lower()
    if at_time is None:
        return None, "I didn't understand that time. Say a time like 9 AM or 17:30."
    if not app or len(app) > 80:
        return None, "Tell me which app to open each day."
    from modules import tool_registry
    action = tool_registry.get("open_app")
    if action is None or action.level == CRITICAL:
        return None, "I can't schedule that app action safely."
    clean, error = action.validate({"name": app})
    if error:
        return None, f"I can't schedule that app: {error}."
    with _lock:
        rows = _load()
        task = {"id": max((int(r["id"]) for r in rows if str(r["id"]).isdigit()), default=0) + 1,
                "kind": "daily", "at_time": at_time, "tool": "open_app", "args": clean,
                "description": description or f"open {app}", "last_run": ""}
        rows.append(task)
        _save(rows)
    _arm_daily()
    log.info("Scheduled task %s created: daily %s %s", task["id"], at_time, task["description"])
    return task, None


def add_battery_below(threshold, message="tell me"):
    try:
        threshold = int(threshold)
    except (TypeError, ValueError):
        return None, "Give me a battery percentage."
    if not 5 <= threshold <= 95:
        return None, "Choose a battery level from 5 to 95 percent."
    with _lock:
        rows = _load()
        task = {"id": max((int(r["id"]) for r in rows if str(r["id"]).isdigit()), default=0) + 1,
                "kind": "battery_below", "threshold": threshold,
                "description": f"tell you when the battery drops below {threshold} percent",
                "armed": True}
        rows.append(task)
        _save(rows)
    _arm_event()
    log.info("Scheduled battery alert %s: below %s percent", task["id"], threshold)
    return task, None


def pending():
    with _lock:
        return list(_load())


def cancel(task_id):
    with _lock:
        rows = _load()
        kept = [row for row in rows if str(row["id"]) != str(task_id)]
        if len(kept) == len(rows):
            return False
        _save(kept)
    _arm_daily()
    _arm_event()
    return True


def _say(message):
    if _notify:
        _notify(message)
        return
    from modules import reminders
    (reminders.on_fire or reminders.default_notify)(message)


def _run(task):
    runner = _runner
    if runner is None:
        _say(f"Scheduled task {task['id']} is due, but Max isn't ready to run it.")
        return
    tool, args, problem = runner.prepare(task["tool"], task["args"])
    if problem is not None:
        _say(f"I couldn't run scheduled task {task['id']}: {problem.message}")
        return
    result = runner.run(tool, args, "approved when scheduled", source="scheduled task")
    _say(result.message if result.success else f"Scheduled task failed: {result.message}")


def _daily_tick():
    global _timer
    now = datetime.now()
    today = now.date().isoformat()
    due = []
    with _lock:
        rows = _load()
        changed = False
        for row in rows:
            if row["kind"] != "daily" or row.get("last_run") == today:
                continue
            hour, minute = map(int, row["at_time"].split(":"))
            if now.hour > hour or (now.hour == hour and now.minute >= minute):
                row["last_run"] = today
                due.append(dict(row))
                changed = True
        if changed:
            _save(rows)
    for task in due:
        log.info("Scheduled task %s fired", task["id"])
        _run(task)
    _arm_daily()


def _arm_daily():
    global _timer
    with _lock:
        if _timer:
            _timer.cancel()
            _timer = None
        rows = [row for row in _load() if row["kind"] == "daily"]
        if _runner is None or not rows:
            return
        delay = min(_next_delay(row["at_time"]) for row in rows)
        _timer = threading.Timer(delay, _daily_tick)
        _timer.daemon = True
        _timer.start()


def check_battery(percent, now=None):
    """Return and mark battery schedules that crossed their threshold; pure input for tests."""
    triggered = []
    with _lock:
        rows = _load()
        changed = False
        for row in rows:
            if row["kind"] != "battery_below":
                continue
            threshold = row["threshold"]
            if percent >= threshold + 5:
                if not row.get("armed", True):
                    row["armed"] = True
                    changed = True
            elif percent < threshold and row.get("armed", True):
                row["armed"] = False
                triggered.append(dict(row))
                changed = True
        if changed:
            _save(rows)
    return triggered


def _battery_tick():
    global _event_timer
    with _lock:
        tasks = [row for row in _load() if row["kind"] == "battery_below"]
    if _runner is None or not tasks:
        _event_timer = None
        return
    try:
        from modules.performance_monitor import battery_info
        reading = battery_info()
        if reading and reading.get("percent") is not None:
            for task in check_battery(reading["percent"]):
                message = f"Battery is below {task['threshold']} percent."
                log.info("Scheduled battery task %s fired", task["id"])
                _say(message)
    except Exception:
        log.exception("Scheduled battery check failed")
    _arm_event()


def _arm_event():
    global _event_timer
    with _lock:
        if _event_timer:
            _event_timer.cancel()
            _event_timer = None
        exists = any(row["kind"] == "battery_below" for row in _load())
        if _runner is None or not exists:
            return
        _event_timer = threading.Timer(CHECK_SECONDS, _battery_tick)
        _event_timer.daemon = True
        _event_timer.start()


def start(runner, notify=None):
    global _runner, _notify
    _runner, _notify = runner, notify
    _arm_daily()
    _arm_event()


def stop():
    global _runner, _notify, _timer, _event_timer
    with _lock:
        for timer in (_timer, _event_timer):
            if timer:
                timer.cancel()
        _timer = _event_timer = None
        _runner = _notify = None
