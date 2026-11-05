import json
from collections import Counter
from datetime import datetime, timedelta

from modules.logger import get_logger
from modules.result import Result

# Activity recall: "what did I do yesterday?", "which apps did I open most this week?"
# Reads logs/actions.jsonl, the log every tool call already writes. No new tracking, no polling.

log = get_logger()

PERIODS = ("today", "yesterday", "this week", "last 7 days", "last week")
FOCUS = ("all", "apps", "songs")

# Tools that only look something up are left out of "what did I do"
LOOKUPS = {"recall", "list_memories", "list_reminders", "list_running_apps", "list_windows", "system_status",
           "cpu_usage", "ram_usage", "disk_space", "battery_status", "gpu_usage", "top_processes",
           "program_memory", "recent_problems", "get_datetime", "health_report", "uptime", "startup_apps",
           "power_plan", "activity_summary", "list_routines"}
# Memory tools are counted, never quoted: their arguments are your facts
PRIVATE = {"remember", "forget_memory", "forget_all_memories"}

SAID = {
    "open_app": "opened", "close_app": "closed", "close_tab": "closed a tab in", "restart_app": "restarted",
    "force_close": "force closed", "end_process": "ended", "new_window": "opened a new window of",
}


def period_range(period, now=None):
    """(start, end) datetimes for a period name."""
    now = now or datetime.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "yesterday":
        return midnight - timedelta(days=1), midnight
    if period == "this week":
        return midnight - timedelta(days=now.weekday()), now
    if period == "last week":
        start = midnight - timedelta(days=now.weekday() + 7)
        return start, start + timedelta(days=7)
    if period == "last 7 days":
        return midnight - timedelta(days=6), now
    return midnight, now


def read_actions(path, start, end):
    """The successful tool calls between start and end, oldest first."""
    entries = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    when = datetime.fromisoformat(entry["time"])
                except (ValueError, KeyError, TypeError):
                    continue
                if start <= when < end and entry.get("success") and entry.get("tool"):
                    entries.append(entry)
    except FileNotFoundError:
        pass
    except OSError as e:
        log.error("Could not read the action log: %s", e)
    return entries


def _app(entry):
    return str((entry.get("args") or {}).get("name") or "").strip().lower()


def _times(n):
    return "once" if n == 1 else "twice" if n == 2 else f"{n} times"


def _join(items):
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def summary(entries, period="today", focus="all"):
    when = period if period in ("today", "yesterday") else f"in {period}" if period.startswith("last") else period
    if not entries:
        return Result(True, f"I didn't do anything for you {when}.")

    opened = Counter(_app(e) for e in entries if e["tool"] == "open_app" and _app(e))
    songs = Counter(str(e["args"].get("query", "")).strip() for e in entries
                    if e["tool"] == "play_music" and (e.get("args") or {}).get("query"))

    if focus == "apps":
        if not opened:
            return Result(True, f"You didn't open any apps through me {when}.")
        top = [f"{name} {_times(n)}" for name, n in opened.most_common(5)]
        return Result(True, f"Most opened {when}: {_join(top)}.")
    if focus == "songs":
        if not songs:
            return Result(True, f"I didn't play any songs {when}.")
        top = [f"{name}" + (f" {_times(n)}" if n > 1 else "") for name, n in songs.most_common(5)]
        return Result(True, f"Songs I played {when}: {_join(top)}.")

    actions = [e for e in entries if e["tool"] not in LOOKUPS]
    parts = []
    for tool, verb in SAID.items():
        apps = Counter(_app(e) for e in actions if e["tool"] == tool and _app(e))
        if apps:
            parts.append(f"{verb} " + _join([f"{a}" + (f" {_times(n)}" if n > 1 else "") for a, n in apps.most_common(4)]))
    if songs:
        parts.append(f"played {len(songs)} song{'s' if len(songs) > 1 else ''}")
    reminders = sum(1 for e in actions if e["tool"] == "set_reminder")
    if reminders:
        parts.append(f"set {reminders} reminder{'s' if reminders > 1 else ''}")
    memories = sum(1 for e in actions if e["tool"] in PRIVATE)
    if memories:
        parts.append(f"changed your memories {_times(memories)}")
    routines = Counter(str(e["args"].get("name")) for e in actions if e["tool"] == "run_routine")
    if routines:
        parts.append("ran " + _join(list(routines)))
    other = len([e for e in actions if e["tool"] not in SAID and e["tool"] not in PRIVATE
                 and e["tool"] not in ("play_music", "set_reminder", "run_routine")])
    if other:
        parts.append(f"{other} other action{'s' if other > 1 else ''}, like volume and media keys")

    count = len(actions)
    if not parts:
        return Result(True, f"{when.capitalize()} you only asked me to look things up, {len(entries)} times.")
    message = f"{when.capitalize()} I did {count} thing{'s' if count != 1 else ''} for you: {_join(parts)}."
    first, last = entries[0]["time"][11:16], entries[-1]["time"][11:16]
    detail = f"  From {first} to {last}. {len(entries) - count} lookups not counted."
    return Result(True, message, detail=detail)


def activity_summary(path, period="today", focus="all", now=None):
    if period not in PERIODS:
        return Result(False, f"I can tell you about {', '.join(PERIODS)}.")
    start, end = period_range(period, now)
    return summary(read_actions(path, start, end), period, focus if focus in FOCUS else "all")
