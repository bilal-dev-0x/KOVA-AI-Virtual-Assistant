import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

import music_library
from modules import media_keys, performance_monitor, reminders, system_control
from modules.fuzzy import best_match
from modules.result import Result
from modules.tool_registry import BASE_DIR, CONTROLLED, CRITICAL, tool

# Max's built-in tools. Importing this module registers them.
# Descriptions are short on purpose: every one is sent with each Gemini request.

_context = {"config": {}, "provider": None}


def setup(config, provider=None):
    """Give the tools the settings and the AI provider (for web_search)."""
    _context["config"] = config or {}
    _context["provider"] = provider


# ---------- apps ----------

def _check_closable(name, window=""):
    return system_control.check_app(name, must_be_running=True)[1]


def _check_restartable(name):
    return system_control.check_app(name)[1]


@tool("Open an app or website by name, e.g. 'chrome', 'vs code', 'youtube'. Any Start menu app works.",
      params={"name": "App or website name"}, confirm_text="open {name}")
def open_app(name: str):
    result = system_control.open_target(name, _context["config"])
    if result.success:
        key, problem = system_control.check_app(name)
        if problem is None:
            result.undo = lambda: system_control.close_app(key)
            result.undo_text = f"close {key}"
    return result


@tool("Close a running app gently, like clicking X, so it can still ask to save work. "
      "Reports how much RAM was freed. Give window to close only one of its windows (see list_windows).",
      params={"name": "App name", "window": "Part of one window's title; leave out to close the whole app"},
      level=CONTROLLED, check=_check_closable,
      # "Should I close the chrome window Softly?" names the window, not just the app
      confirm_text=lambda a: (f"close the {a['name']} window {system_control.spoken_title(a['window'])}"
                              if a.get("window") else f"close {a['name']}"))
def close_app(name: str, window: str = ""):
    if window:
        return system_control.close_app(name, window=window)
    before = system_control.app_memory_mb(name)
    result = system_control.close_app(name)
    # Background processes (Chrome, Brave) keep their RAM, so only what ended counts
    freed = before - system_control.app_memory_mb(name)
    if result.success and freed >= 1:
        first, dot, rest = result.message.partition(". ")
        result.message = f"{first}, freeing about {freed:.0f} MB of RAM" + (f". {rest}" if dot else "")
    if result.success:
        result.undo = lambda: system_control.open_target(name, _context["config"])
        result.undo_text = f"open {name} again"
    return result


def _check_tab(name, title):
    if not system_control.is_browser(name):
        return Result(False, f"{name} isn't a browser I know, so I can't close its tabs.")
    return None


@tool("Close one browser tab, e.g. a YouTube video, leaving the window's other tabs open. Only the tab "
      "showing in a window can be closed: give part of that window's title (see list_windows).",
      params={"name": "Browser name, e.g. chrome", "title": "Part of the window title, e.g. 'Softly'"},
      level=CONTROLLED, check=_check_tab,
      confirm_text=lambda a: f"close the {system_control.spoken_title(a['title'])} tab in {a['name']}")
def close_tab(name: str, title: str):
    return system_control.close_tab(name, title)


@tool("The titles of an app's open windows, e.g. each Chrome window.", params={"name": "App name"})
def list_windows(name: str):
    key, entry = system_control._resolve_app(name)
    if key is None or not entry.get("process"):
        return Result(False, f"I don't know the windows of {name}.")
    titles = [system_control.short_title(t) for _, t in system_control.app_windows(entry["process"])]
    if not titles:
        return Result(True, f"{key} has no open windows.")
    return Result(True, f"{key} has {len(titles)} window{'s' if len(titles) > 1 else ''}: " + "; ".join(titles) + ".")


@tool("Open the Windows Start menu, like pressing the Windows key.")
def open_start_menu():
    media_keys.press("windows")
    return Result(True, "Start menu is open.")


@tool("Close and reopen an app.", params={"name": "App name"},
      level=CONTROLLED, confirm_text="restart {name}", check=_check_restartable)
def restart_app(name: str):
    return system_control.restart_app(name, _context["config"])


@tool("List which of the user's configured apps are running.")
def list_running_apps():
    return system_control.running_apps()


# ---------- system ----------

@tool("Current CPU, RAM, disk and battery figures.")
def system_status():
    return performance_monitor.system_status()


@tool("Current CPU usage percent.")
def cpu_usage():
    return performance_monitor.cpu_report()


@tool("Current RAM usage.")
def ram_usage():
    return performance_monitor.ram_report()


@tool("Free space on each drive.")
def disk_space():
    return performance_monitor.disk_report()


@tool("Battery level and charging state.")
def battery_status():
    return performance_monitor.battery_report()


@tool("GPU usage percent and which programs are using the GPU.")
def gpu_usage():
    return performance_monitor.gpu_report()


@tool("The programs using the most RAM or CPU, with all processes of a program added together. "
      "Use it to find out why the PC is slow.",
      params={"sort_by": ("What to sort by", ["ram", "cpu"]), "count": "How many to list (1-15)"})
def top_processes(sort_by: str = "ram", count: int = 5):
    return performance_monitor.top_processes(sort_by, max(1, min(count, 15)))


@tool("How much RAM one program is using, e.g. 'chrome', 'vs code', 'claude'.",
      params={"name": "App or process name"})
def program_memory(name: str):
    return performance_monitor.program_memory(name)


@tool("End a running program by its process name (e.g. 'discord.exe'), gently. "
      "Prefer close_app for apps it knows. Windows system processes are refused.",
      params={"process_name": "Process name from top_processes"},
      level=CRITICAL, confirm_text="end the process {process_name}",
      check=lambda process_name: system_control.check_process(process_name))
def end_process(process_name: str):
    return system_control.end_process(process_name)


@tool("Force a stuck app or process to close at once. Unsaved work in it is lost. Use it only when "
      "close_app or end_process already failed and the user insists, or asks to force it.",
      params={"name": "App name or process name"},
      level=CRITICAL, confirm_text="force close {name}. Anything unsaved in it will be lost",
      check=lambda name: system_control.check_force(name)[1])
def force_close(name: str):
    return system_control.force_close(name)


# ---------- voice ----------

CONFIG_FILE = Path(BASE_DIR) / "config.json"


def save_setting(key, value):
    """Change one setting in config.json and keep everything else as it is."""
    import json
    _context["config"][key] = value
    path = Path(CONFIG_FILE)
    if not path.exists():
        return
    config = json.loads(path.read_text(encoding="utf-8"))
    config[key] = value
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


@tool("Switch Max's speaking voice between male and female. It stays that way next time too.",
      params={"gender": ("Which voice", ["male", "female"])})
def set_voice(gender: str):
    from modules import voice_handler
    before = voice_handler.current_voice()
    _use_voice(gender)
    result = Result(True, f"Okay, I'll use the {gender} voice now.")
    result.undo = lambda: _use_voice(before) or Result(True, f"Back to the {before} voice.")
    result.undo_text = f"go back to the {before} voice"
    return result


def _use_voice(gender):
    from modules import voice_handler
    voice_handler.set_voice(gender)
    save_setting("VOICE", gender)


# ---------- media ----------

def _press(key, times, message, undo_key=None):
    media_keys.press(key, times)
    result = Result(True, message)
    if undo_key:
        result.undo = lambda: _press(undo_key, times, "Volume put back.")
        result.undo_text = "put the volume back"
    return result


@tool("Turn the volume up.", params={"percent": "How much, in percent (default 10)"})
def volume_up(percent: int = 10):
    return _press("volume_up", max(1, min(percent, 100)) // 2 or 1, "Volume up.", undo_key="volume_down")


@tool("Turn the volume down.", params={"percent": "How much, in percent (default 10)"})
def volume_down(percent: int = 10):
    return _press("volume_down", max(1, min(percent, 100)) // 2 or 1, "Volume down.", undo_key="volume_up")


@tool("Mute or unmute the sound.", confirm_text="mute or unmute the sound")
def mute():
    result = media_keys.mute()
    result.undo = media_keys.mute
    result.undo_text = "toggle mute back"
    return result


@tool("Pause or resume whatever is playing.", confirm_text="pause or resume the music")
def play_pause():
    return media_keys.play_pause()


@tool("Skip to the next track.", confirm_text="skip to the next track")
def next_track():
    return media_keys.next_track()


@tool("Go back to the previous track.")
def previous_track():
    return media_keys.previous_track()


@tool("Play a song: from the user's music library if it's there, otherwise a YouTube search.",
      params={"query": "Song or artist"}, confirm_text="play {query}")
def play_music(query: str):
    query = re.sub(r"^(the )?song ", "", query.lower())
    query = re.sub(r" (song|on youtube)$", "", query)
    config = _context["config"]

    song = best_match(query, music_library.library.keys())
    if song:
        system_control.open_url(music_library.library[song], config)
        return Result(True, f"Playing {song.split(' - ')[0]}", detail=song)

    video = first_youtube_video(query)
    if video:
        system_control.open_url(video, config)
        return Result(True, f"Playing {query} from YouTube.")
    system_control.open_url(f"https://www.youtube.com/results?search_query={quote_plus(query)}", config)
    return Result(True, f"{query} isn't in your library, so I searched YouTube for it.")


def first_youtube_video(query, timeout=6):
    """The watch link of YouTube's first search result, or None.

    One plain page request: no API key, nothing is downloaded or run.
    """
    import urllib.request
    url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            page = response.read(2_000_000).decode("utf-8", "replace")
    except Exception:
        return None
    match = re.search(r'"videoId":"([\w-]{11})"', page)
    return f"https://www.youtube.com/watch?v={match.group(1)}" if match else None


# ---------- web ----------

SEARCH_SITES = {
    "google": "https://www.google.com/search?q=",
    "youtube": "https://www.youtube.com/results?search_query=",
}


@tool("Open a search results page in the browser for the user to look at.",
      params={"query": "What to search for", "site": ("Where to search", list(SEARCH_SITES)),
              "browser": "Browser to use if the user named one, e.g. 'brave'; leave out for the default"})
def open_web_search(query: str, site: str = "google", browser: str = ""):
    url = SEARCH_SITES[site] + quote_plus(query)
    if browser:
        opened = system_control.open_in_browser(browser, url)
        return Result(True, f"Searching {site} for {query}. {opened.message}.")
    system_control.open_url(url, _context["config"])
    return Result(True, f"Searching {site} for {query}")


@tool("Open another window of an app. Browsers get a new browser window.", params={"name": "App name"})
def new_window(name: str):
    return system_control.new_window(name, _context["config"])


@tool("Search the files on this PC with Windows' own search, in File Explorer.",
      params={"query": "File or folder name to look for"})
def search_files(query: str):
    import os
    from urllib.parse import quote
    home = os.path.expanduser("~")
    os.startfile(f"search-ms:query={quote(query)}&crumb=location:{quote(home)}")
    return Result(True, f"Searching your files for {query}")


@tool("Max's own recent warnings and errors from its log, newest last.",
      params={"count": "How many to read (1-10)"})
def recent_problems(count: int = 5):
    from modules.logger import LOG_DIR
    path = Path(LOG_DIR) / "kova.log"
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return Result(True, "I have no log yet.")
    problems = [line for line in lines if line.startswith(today) and (" WARNING " in line or " ERROR " in line)]
    if not problems:
        return Result(True, "No problems in my log today.")
    # The same problem many times reads as one line: "Natural voice failed, 4 times"
    counts = {}
    for line in problems:
        text = line.split(" ", 3)[-1].split(":")[0].split(" (")[0][:80]
        counts[text] = counts.get(text, 0) + 1
    latest = list(counts)[-max(1, min(count, 10)):]
    spoken = [text + (f", {counts[text]} times" if counts[text] > 1 else "") for text in latest]
    return Result(True, f"I logged {len(problems)} problems today. The latest: " + "; ".join(spoken) + ".",
                  detail="\n".join(problems[-10:]))


@tool("Look something up on the live web (news, weather, prices, sports, recent events) "
      "and get a short answer with current facts.",
      params={"query": "The question to look up"})
def web_search(query: str):
    provider = _context["provider"]
    if provider is None:
        return Result(False, "Web search needs Gemini, and it isn't set up.")
    answer = provider.search(query)
    if not answer:
        return Result(False, "Live web search isn't available with this Gemini key. "
                             "Offer to open a Google search in the browser instead (open_web_search).")
    return Result(True, answer)


@tool("The current date and time on this PC.", confirm_text="tell you the time")
def get_datetime():
    return Result(True, datetime.now().strftime("%A %d %B %Y, %I:%M %p"))


# ---------- reminders ----------

TIMER_WORDS = {"timer", "alarm", "times up", "time's up", "time is up", "timer done"}

@tool("Set a reminder or timer. Give either minutes from now or a clock time.",
      params={"message": "What to remind the user about",
              "minutes": "Minutes from now (can be a fraction)",
              "at_time": "Clock time today as HH:MM in 24-hour format, e.g. '17:30'"},
      confirm_text=lambda a: (f"set a reminder for {a['at_time']}: {a['message']}" if a.get("at_time") else
                              f"set a reminder in {reminders.describe_delay(a.get('minutes', 0) * 60)}: {a['message']}"))
def set_reminder(message: str, minutes: float = 0, at_time: str = ""):
    if at_time:
        seconds = reminders.seconds_until(at_time)
        if seconds is None:
            return Result(False, f"I don't understand the time {at_time}.")
    else:
        seconds = minutes * 60
    if seconds <= 0:
        return Result(False, "Tell me when, for example in 10 minutes or at 5 pm.")
    if seconds > 7 * 24 * 3600:
        return Result(False, "I can only set reminders up to a week ahead.")

    delay = reminders.describe_delay(seconds)
    if message.lower().strip(" .!") in TIMER_WORDS:
        # A plain timer: "I'll remind you to Timer" sounded broken
        reminder = reminders.add(f"your {delay} timer is done", seconds)
        result = Result(True, f"Timer set for {delay}, until {reminder.when()}.")
    else:
        reminder = reminders.add(message, seconds)
        result = Result(True, f"Okay, I'll remind you to {message} in {delay}, at {reminder.when()}.")
    result.undo = lambda: Result(True, f"Cancelled the reminder to {message}." if reminders.cancel(reminder.id)
                                 else "That reminder already went off.")
    result.undo_text = "cancel that reminder"
    return result


@tool("List the reminders that haven't gone off yet.", confirm_text="read your reminders")
def list_reminders():
    waiting = reminders.pending()
    if not waiting:
        return Result(True, "You have no reminders.")
    lines = [f"number {r.id}: {r.message} at {r.when()}" for r in waiting]
    return Result(True, f"You have {len(waiting)} reminder{'s' if len(waiting) > 1 else ''}: " + "; ".join(lines) + ".")


@tool("Cancel a reminder by its number from list_reminders, or all reminders if no number is given.",
      params={"reminder_id": "Reminder number; leave out to cancel all"})
def cancel_reminder(reminder_id: int = 0):
    count = reminders.cancel(reminder_id or None)
    if not count:
        return Result(False, "There was no such reminder." if reminder_id else "You have no reminders.")
    return Result(True, f"Cancelled {count} reminder{'s' if count > 1 else ''}.")


# ---------- memory ----------

def _memory():
    from modules import memory_engine
    return memory_engine


def _check_fact(fact):
    engine = _memory()
    if engine.looks_secret(fact):
        return Result(False, engine.SECRET_MESSAGE)
    if not fact.strip(" ."):
        return Result(False, "There's nothing to remember.")
    return None


def _check_fact_id(fact_id):
    if _memory().get_memory().get(fact_id) is None:
        return Result(False, f"I don't have a memory number {fact_id}.")
    return None


@tool("Save a lasting fact or preference about the user, in their words, e.g. 'I like lo-fi when coding'. "
      "Use it when the user asks you to remember something, or states a lasting preference. "
      "Never for passwords, PINs, keys or card numbers.",
      params={"fact": "The fact, in the user's own words"},
      level=CONTROLLED, confirm_text="save this to my memory: {fact}", check=_check_fact)
def remember(fact: str):
    engine = _memory()
    store = engine.get_memory()
    try:
        saved, replaced = store.remember(fact)
    except ValueError as e:  # SecretError included
        return Result(False, str(e))
    said = engine.as_you(saved.text)
    if replaced and replaced.text != saved.text:
        # "Updated. Your project folder is E:\Work now, not D:\Code."
        old = engine.value_of(replaced.text) or engine.as_you(replaced.text)
        result = Result(True, f"Updated. {_capital(said)} now, not {old}.")
        result.undo = lambda: store.restore(replaced) and None or Result(
            True, f"Okay, {engine.as_you(replaced.text)} again.")
    elif replaced:
        result = Result(True, f"I already knew that {said}.")
    else:
        result = Result(True, f"Got it. I'll remember that {said}.")
        result.undo = lambda: store.forget(saved.id) and None or Result(True, f"Okay, I forgot that {said}.")
    result.undo_text = "forget what I just saved"
    return result


@tool("Look up what the user told you before about something, e.g. 'project folder'. "
      "Returns the matching memories with their numbers.",
      params={"query": "What to look up"})
def recall(query: str):
    engine = _memory()
    store = engine.get_memory()
    found = [fact for _, fact in store.find(query)]
    if not found:
        return Result(False, f"I don't have anything saved about {query}.")
    store.used(found)
    said = [engine.as_you(f.text) for f in found]
    message = _capital(said[0]) + "." if len(said) == 1 else "Here's what I know: " + "; ".join(said) + "."
    return Result(True, message, detail="\n".join(f"  #{f.id}: {f.text}" for f in found))


@tool("List everything the user asked you to remember, with memory numbers.")
def list_memories():
    engine = _memory()
    facts = engine.get_memory().all()
    if not facts:
        return Result(True, "I don't know anything about you yet. Say remember, then something about yourself.")
    said = [engine.as_you(f.text) for f in facts]
    shown = "; ".join(said[:5])
    more = f"; and {len(facts) - 5} more" if len(facts) > 5 else ""
    count = f"{len(facts)} thing{'s' if len(facts) > 1 else ''}"
    return Result(True, f"I remember {count} about you: {shown}{more}.",
                  detail="\n".join(f"  #{f.id}: {f.text}" for f in facts))


@tool("Delete one memory by its number from recall or list_memories.",
      params={"fact_id": "Memory number"},
      level=CONTROLLED, confirm_text="forget memory number {fact_id}", check=_check_fact_id)
def forget_memory(fact_id: int):
    engine = _memory()
    store = engine.get_memory()
    fact = store.forget(fact_id)
    if fact is None:
        return Result(False, f"I don't have a memory number {fact_id}.")
    said = engine.as_you(fact.text)
    result = Result(True, f"Okay, I forgot that {said}.")
    result.undo = lambda: store.restore(fact) and None or Result(True, f"Okay, I remember again that {said}.")
    result.undo_text = "remember that again"
    return result


@tool("Delete every memory about the user. Only when they clearly ask to forget everything.",
      level=CRITICAL, confirm_text="forget everything I know about you. This can't be undone")
def forget_all_memories():
    count = _memory().get_memory().forget_all()
    if not count:
        return Result(True, "There was nothing to forget.")
    things = "the one thing" if count == 1 else f"all {count} things"
    return Result(True, f"Done. I forgot {things} I knew about you.")


def _capital(text):
    return text[:1].upper() + text[1:]


# ---------- system intelligence (Day 3) ----------

@tool("A full health check of the PC: CPU, RAM, disks, battery, uptime and the 3 programs using the most RAM, "
      "with anything worrying named. Use it for 'how is my PC doing' or 'why is my PC slow'.",
      confirm_text="check your PC's health")
def health_report():
    return performance_monitor.health_report()


@tool("How long the PC has been on since it was last started.")
def uptime():
    from modules import system_info
    return system_info.uptime_report()


@tool("The apps that start with Windows (read only; Max never changes them).")
def startup_apps():
    from modules import system_info
    return system_info.startup_report()


@tool("The Windows power plan in use.")
def power_plan():
    from modules import system_info
    return system_info.power_plan_report()


@tool("Switch the Windows power plan. Undo switches back to the plan before.",
      params={"plan": ("Which plan", ["balanced", "high performance", "power saver", "ultimate performance"])},
      level=CONTROLLED, confirm_text="switch the power plan to {plan}")
def set_power_plan(plan: str):
    from modules import system_info
    return system_info.set_power_plan(plan)


# ---------- alerts ----------

@tool("Turn Max's PC alerts on or off: low battery, RAM almost full, a drive almost full. "
      "Checked once a minute; each alert repeats at most every 30 minutes. Remembered next time too.",
      params={"on": "True to turn alerts on, False to turn them off"})
def set_alerts(on: bool):
    from modules import alerts
    was_on = alerts.running()
    _switch_alerts(on)
    limits = alerts.thresholds(_context["config"])
    if on:
        message = (f"Alerts are on. I'll warn you when the battery drops below {limits['ALERT_BATTERY_PERCENT']:.0f} percent, "
                   f"RAM goes over {limits['ALERT_RAM_PERCENT']:.0f} percent, or a drive has less than "
                   f"{limits['ALERT_DISK_FREE_PERCENT']:.0f} percent free.")
    else:
        message = "Alerts are off."
    result = Result(True, message)
    result.undo = lambda: _switch_alerts(was_on) or Result(True, f"Alerts are {'on' if was_on else 'off'} again.")
    result.undo_text = f"turn alerts {'on' if was_on else 'off'} again"
    return result


def _switch_alerts(on):
    from modules import alerts
    save_setting("ALERTS_ON", bool(on))
    if on:
        alerts.start(_context["config"])
    else:
        alerts.stop()


# ---------- routines ----------

def _routines():
    from modules import routines
    found, problems, file = routines.cached()
    return routines, found, problems, file


def _check_routine(name):
    routines, found, _, _ = _routines()
    if routines.find(found, name) is None:
        names = ", ".join(r.name for r in found) or "none yet"
        return Result(False, f"I don't have a routine called {name}. Your routines: {names}.")
    return None


def _describe_routine(args):
    routines, found, _, _ = _routines()
    routine = routines.find(found, args.get("name", ""))
    return f"run {routine.name}: {routine.describe()}" if routine else f"run the routine {args.get('name')}"


@tool("List the user's routines: named sets of steps, like 'coding mode'.")
def list_routines():
    routines, found, problems, file = _routines()
    return routines.list_report(found, problems, file)


@tool("Run one of the user's routines by name, e.g. 'coding mode'. Its steps run in order and it stops "
      "at the first step that fails. See list_routines for the names.",
      params={"name": "Routine name"},
      level=CONTROLLED, confirm_text=_describe_routine, check=_check_routine)
def run_routine(name: str):
    routines, found, _, _ = _routines()
    runner, confirm = _context.get("runner"), _context.get("confirm")
    if runner is None:
        return Result(False, "Routines need Max's action runner, which isn't set up.")
    # Approved already, as one plan: only critical steps ask again
    return routines.run(routines.find(found, name), runner, confirm or (lambda q: False), approved=True)


# ---------- activity ----------

@tool("What Max did for the user in a period: apps opened and closed, songs played, reminders set. "
      "Read from Max's own action log.",
      params={"period": ("Which period", ["today", "yesterday", "this week", "last 7 days", "last week"]),
              "focus": ("What to count", ["all", "apps", "songs"])})
def activity_summary(period: str = "today", focus: str = "all"):
    from modules import actions, activity
    return activity.activity_summary(actions.ACTIONS_LOG, period, focus)


# ---------- folders and terminals ----------

def _check_folder(path):
    if not system_control.spoken_path(path):
        return Result(False, f"I can't find a folder called {path}.")
    return None


@tool("Open a folder in File Explorer. Give a full path like 'D:\\Code' or say it like 'D drive coding arc'.",
      params={"path": "Folder path"}, confirm_text="open the folder {path}", check=_check_folder)
def open_folder(path: str):
    return system_control.open_folder(system_control.spoken_path(path))


@tool("Close the open terminal windows (PowerShell, Command Prompt), never the one Max runs in.",
      level=CONTROLLED, confirm_text="close the terminal windows")
def close_terminals():
    return system_control.close_terminals()
