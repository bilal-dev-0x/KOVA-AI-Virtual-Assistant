"""Day 3 steps 3-8: recall by meaning, memories in agent requests, system intelligence,
alerts, routines and activity recall."""
import json
from datetime import datetime

import pytest

from brain.base import Reply, ToolCall
from desktop_agent import Agent
from modules import actions, activity, alerts, memory_engine, performance_monitor, routines, system_info, tool_registry
from modules.result import Result
from tests.conftest import FakeProvider

CONFIG = {"ASSISTANT_NAME": "Max", "WAKE_WORDS": ["max"]}


class Asked:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.questions = []

    def __call__(self, question):
        self.questions.append(question)
        return self.answers.pop(0) if self.answers else False


def agent(*answers, provider=None, config=None):
    asked = Asked(*answers)
    return Agent({**CONFIG, **(config or {})}, confirm=asked, provider=provider), asked


def store():
    return memory_engine.get_memory()


# ---------- 3. recall by meaning ----------

@pytest.mark.parametrize("fact, query", [
    ("My project folder is D:\\Code", "where do I keep my code"),
    ("My project folder is D:\\Code", "which directory has my projects"),
    ("I love lo-fi songs when coding", "what music do I like"),
    ("I am a Python learner", "am I learning pyton"),
    ("My birthday is 12 March", "when was I born"),
    ("I work at Systems Ltd", "where is my job"),
])
def test_recall_by_meaning(fact, query):
    store().remember(fact)
    store().remember("My car is a white Civic")
    found = store().find(query, limit=1)
    assert found and found[0][1].text == fact


def test_unrelated_questions_find_nothing():
    store().remember("My project folder is D:\\Code")
    assert store().find("what's my favourite colour") == []


def test_stem():
    assert memory_engine.stem("folders") == memory_engine.stem("folder")
    assert memory_engine.stem("learner") == "learn"
    assert memory_engine.stem("cities") == "city"


# ---------- 4. memories in agent requests ----------

def test_matching_memories_are_added_to_the_request():
    store().remember("My project folder is D:\\Code")
    store().remember("I like lo-fi when coding")
    provider = FakeProvider([Reply(text="Done.")])
    max_, _ = agent(provider=provider)
    max_.handle("open my project folder and then play something")
    sent = provider.fake_session.sent[0]
    assert sent.startswith("(Known about the user, in their words: My project folder is D:\\Code")
    assert "lo-fi" not in sent  # only facts that match the request


def test_requests_without_a_match_carry_nothing():
    store().remember("My project folder is D:\\Code")
    provider = FakeProvider([Reply(text="Why did the chicken...")])
    max_, _ = agent(provider=provider)
    max_.handle("tell me a joke and the weather")
    assert "Known about the user" not in provider.fake_session.sent[0]


def test_memory_in_requests_can_be_switched_off():
    store().remember("My project folder is D:\\Code")
    provider = FakeProvider([Reply(text="Done.")])
    max_, _ = agent(provider=provider, config={"MEMORY_IN_AI_REQUESTS": False})
    max_.handle("open my project folder and then play something")
    assert "Known about the user" not in provider.fake_session.sent[0]


def test_memory_replies_stay_out_of_the_recent_commands_note():
    store().remember("My bank is in Gulberg")
    store().remember("I like lo-fi")
    provider = FakeProvider([Reply(text="Okay.")])
    max_, _ = agent(provider=provider)
    max_.handle("what do you know about me")
    max_.handle("remember my sister is called Ayesha")
    max_.handle("tell me a joke and then the time")
    sent = provider.fake_session.sent[0]
    assert "(a memory command: list memories)" in sent and "(a memory command: remember)" in sent
    assert "Gulberg" not in sent and "Ayesha" not in sent


def test_system_prompt_asks_for_auto_memory():
    provider = FakeProvider([Reply(text="Okay.")])
    max_, _ = agent(provider=provider)
    max_.handle("tell me a joke and then the time")
    assert "remember tool" in provider.system_prompt and "Never save passwords" in provider.system_prompt


def test_auto_memory_still_asks_first():
    provider = FakeProvider([Reply(calls=[ToolCall("remember", {"fact": "I code in Python every night"})]),
                             Reply(text="Noted.")])
    max_, asked = agent(False, provider=provider)
    max_.handle("I code in Python every night so keep that in mind")
    assert asked.questions == ["Should I save this to my memory: I code in Python every night?"]
    assert store().count() == 0


# ---------- 5. health report, uptime, startup apps, power plans ----------

@pytest.fixture
def healthy(monkeypatch):
    monkeypatch.setattr(performance_monitor, "cpu_percent", lambda interval=1.0: 12.0)
    monkeypatch.setattr(performance_monitor, "ram_info", lambda: {"used_gb": 6.0, "total_gb": 16.0, "percent": 38.0})
    monkeypatch.setattr(performance_monitor, "disk_info",
                        lambda: [{"drive": "C:", "free_gb": 120.0, "total_gb": 240.0, "percent": 50.0}])
    monkeypatch.setattr(performance_monitor, "battery_info", lambda: {"percent": 80.0, "plugged": True})
    monkeypatch.setattr(performance_monitor, "_program_totals", lambda: {"chrome.exe": 2100.0, "Code.exe": 900.0})
    monkeypatch.setattr(system_info, "uptime_seconds", lambda: 93784)


def test_health_report_when_all_is_well(healthy):
    result = performance_monitor.health_report()
    assert result.message == ("CPU 12 percent, RAM 38 percent, battery 80 percent, on for 1 day, 2 hours and 3 minutes. "
                              "Everything looks healthy. chrome uses the most RAM, 2.1 gigabytes.")
    assert "Uptime" in result.detail and "#2 RAM  Code.exe" in result.detail


def test_health_report_names_the_worries(healthy, monkeypatch):
    monkeypatch.setattr(performance_monitor, "ram_info", lambda: {"used_gb": 15.0, "total_gb": 16.0, "percent": 93.0})
    monkeypatch.setattr(performance_monitor, "disk_info",
                        lambda: [{"drive": "C:", "free_gb": 9.0, "total_gb": 240.0, "percent": 96.0}])
    message = performance_monitor.health_report().message
    assert "Watch out: RAM is at 93 percent, and drive C: has only 9 gigabytes free." in message


@pytest.mark.parametrize("seconds, said", [(30, "less than a minute"), (60, "1 minute"), (7200, "2 hours"),
                                           (90061, "1 day, 1 hour and 1 minute")])
def test_say_duration(seconds, said):
    assert system_info.say_duration(seconds) == said


def test_startup_report(monkeypatch):
    monkeypatch.setattr(system_info, "startup_apps", lambda: [("Discord", "registry", True),
                                                              ("OneDrive", "registry", True),
                                                              ("Steam", "registry", False)])
    result = system_info.startup_report()
    assert result.message == "2 apps start with Windows: Discord, OneDrive. 1 more is switched off."
    assert "Max only reads this list" in result.detail


POWERCFG_LIST = """
Existing Power Schemes (* Active)
-----------------------------------
Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced) *
Power Scheme GUID: 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c  (High performance)
Power Scheme GUID: a1841308-3bae-4152-9a37-4ff3d8a9d01b  (Power saver)
"""


@pytest.fixture
def powercfg(monkeypatch):
    """A fake powercfg: /list shows the plans, /setactive moves the star. Records every call."""
    state = {"list": POWERCFG_LIST, "calls": []}

    def run(*args):
        state["calls"].append(args)
        if args[0] == "/setactive":
            lines = [line.replace(" *", "") for line in state["list"].splitlines()]
            state["list"] = "\n".join(line + " *" if args[1] in line else line for line in lines)
        return 0, state["list"]
    monkeypatch.setattr(system_info, "_powercfg", run)
    return state


def test_power_plan_report(powercfg):
    assert system_info.power_plan_report().message == "You're on the Balanced power plan."


def test_switch_power_plan_and_undo(powercfg):
    result = system_info.set_power_plan("high performance")
    assert result.message == "Switched to the High performance power plan."
    assert ("/setactive", "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c") in powercfg["calls"]
    assert result.undo().message == "Back on the Balanced plan."
    assert system_info.active_plan()[1] == "Balanced"


def test_power_plan_this_pc_does_not_have(powercfg):
    result = system_info.set_power_plan("ultimate performance")
    assert not result.success and "doesn't have the ultimate performance plan" in result.message
    assert all(call[0] == "/list" for call in powercfg["calls"])


def test_power_plan_only_takes_known_names(powercfg):
    assert tool_registry.get("set_power_plan").validate({"plan": "x & del C:"})[1]
    assert not system_info.set_power_plan("x & del C:").success
    assert all(call[0] == "/list" for call in powercfg["calls"])


def test_set_power_plan_is_controlled():
    assert tool_registry.get("set_power_plan").level == "controlled"
    assert tool_registry.get("startup_apps").level == "safe"


@pytest.mark.parametrize("said, command", [
    ("how's my PC", "health report"), ("how is my laptop doing", "health report"),
    ("Max, why is my computer so slow", "health report"), ("health check", "health report"),
    ("what's my uptime", "uptime"), ("how long has my pc been on", "uptime"),
    ("what starts with windows", "startup apps"), ("show my startup apps", "startup apps"),
    ("switch to high performance", "set power plan"), ("power saver mode", "set power plan"),
    ("set the power plan to balanced", "set power plan"), ("what's my power plan", "power plan"),
    ("which power plan am i on", "power plan"),
    ("turn on alerts", "alerts"), ("turn alerts off", "alerts"), ("disable alerts", "alerts"),
    ("are alerts on", "alerts status"),
    ("what routines do i have", "list routines"), ("list my routines", "list routines"),
    ("what did i do yesterday", "activity"), ("what have you done for me today", "activity"),
    ("which apps did i open most this week", "activity apps"), ("what songs did i play today", "activity songs"),
    ("system status", "system status"), ("how is my cpu and ram", "cpu and ram"),
])
def test_day3_phrases(said, command):
    max_, _ = agent()
    assert max_.match(max_.normalize(said))[0].name == command


def test_switch_power_plan_asks_first(powercfg):
    max_, asked = agent(True)
    assert max_.handle("switch to battery saver").success
    assert asked.questions == ["Switch to the power saver power plan?"]
    assert max_.handle("undo").message == "Undone. Back on the Balanced plan."


# ---------- 6. alerts ----------

LIMITS = {"ALERT_BATTERY_PERCENT": 20, "ALERT_RAM_PERCENT": 90, "ALERT_DISK_FREE_PERCENT": 10}
CALM = {"battery": {"percent": 80, "plugged": False}, "ram": 40, "top_app": None,
        "disks": [{"drive": "C:", "free_gb": 100.0, "percent": 50.0}]}


def test_no_alerts_when_calm():
    assert alerts.check(CALM, LIMITS) == []


def test_each_alert():
    read = {"battery": {"percent": 15, "plugged": False}, "ram": 94, "top_app": "chrome",
            "disks": [{"drive": "C:", "free_gb": 8.0, "percent": 96.5}]}
    assert alerts.check(read, LIMITS) == [
        ("battery", "Battery is at 15 percent. Plug in the charger."),
        ("ram", "RAM is at 94 percent. chrome is using the most."),
        ("disk C:", "Drive C: is almost full: 8 gigabytes free."),
    ]


def test_low_battery_while_charging_is_fine():
    assert alerts.check({**CALM, "battery": {"percent": 10, "plugged": True}}, LIMITS) == []


def test_cooldown():
    alerts._last_said.clear()
    found = [("ram", "RAM is high.")]
    assert alerts.due(found, now=1000) == ["RAM is high."]
    assert alerts.due(found, now=1000 + 60) == []
    assert alerts.due(found, now=1000 + 30 * 60) == ["RAM is high."]


def test_check_once_says_it(monkeypatch):
    alerts._last_said.clear()
    said = []
    monkeypatch.setattr(alerts, "readings", lambda: {**CALM, "ram": 95, "top_app": "chrome"})
    monkeypatch.setattr(alerts, "on_alert", said.append)
    alerts.check_once({"ALERT_RAM_PERCENT": 90})
    alerts.check_once({"ALERT_RAM_PERCENT": 90})
    assert said == ["RAM is at 95 percent. chrome is using the most."]


def test_turn_alerts_on_and_off(monkeypatch):
    saved = {}
    from modules import tools
    monkeypatch.setattr(tools, "save_setting", lambda key, value: saved.update({key: value}))
    max_, _ = agent()
    assert max_.handle("turn on alerts").message.startswith("Alerts are on. I'll warn you when the battery drops below 20")
    assert alerts.running() and saved == {"ALERTS_ON": True}
    assert max_.handle("are alerts on").message == "Alerts are on."
    assert max_.handle("turn off alerts").message == "Alerts are off."
    assert not alerts.running() and saved == {"ALERTS_ON": False}


# ---------- 7. routines ----------

def write_routines(path, items):
    path.write_text(json.dumps({"routines": items}), encoding="utf-8")
    routines._cache["key"] = None


@pytest.fixture
def ran(monkeypatch):
    """Fake tools for routine steps; records what ran."""
    from modules import tools
    done = []
    monkeypatch.setattr(tools.system_control, "open_target",
                        lambda name, config: done.append(("open", name)) or Result(True, f"Opening {name}"))
    monkeypatch.setattr(tools.system_control, "check_app", lambda name, must_be_running=False: (None, Result(False, "x")))
    monkeypatch.setattr(tools.media_keys, "press", lambda key, times=1: done.append(("key", key)))
    return done


CODING = {"name": "coding mode", "phrases": ["coding mode", "start coding"],
          "steps": [{"tool": "open_app", "args": {"name": "vs code"}},
                    {"tool": "open_app", "args": {"name": "terminal"}},
                    {"tool": "set_power_plan", "args": {"plan": "high performance"}}]}


def test_bad_steps_are_reported_not_guessed():
    found, problems = routines.parse({"routines": [
        CODING,
        {"name": "broken", "steps": [{"tool": "launch_rockets"}]},
        {"name": "bad args", "steps": [{"tool": "set_power_plan", "args": {"plan": "turbo"}}]},
        {"name": "", "steps": []},
    ]})
    assert [r.name for r in found] == ["coding mode"]
    assert problems[0] == "broken: step 1: there is no tool called launch_rockets"
    assert problems[1].startswith("bad args: step 1 (set_power_plan):")
    assert problems[2] == "Routine 4 has no name."


@pytest.mark.parametrize("said", ["coding mode", "start coding mode", "Max, start coding", "run my coding mode routine",
                                  "coding mode please"])
def test_routine_phrases(said, tmp_path):
    write_routines(routines.ROUTINES_FILE, [CODING])
    max_, _ = agent()
    assert routines.find(routines.cached()[0], max_.normalize(said)).name == "coding mode"


def test_routine_runs_with_one_approval(tmp_path, ran, powercfg):
    write_routines(routines.ROUTINES_FILE, [CODING])
    max_, asked = agent(True)
    result = max_.handle("coding mode")
    assert asked.questions == ["Should I run coding mode: open vs code, open terminal "
                               "and switch the power plan to high performance?"]
    assert result.message == "Coding mode is ready: 3 steps done."
    assert ran == [("open", "vs code"), ("open", "terminal")]
    assert system_info.active_plan()[1] == "High performance"


def test_routine_declined_runs_nothing(tmp_path, ran, powercfg):
    write_routines(routines.ROUTINES_FILE, [CODING])
    max_, _ = agent(False)
    assert max_.handle("coding mode").message == "Okay, I didn't run it."
    assert ran == []


def test_routine_stops_at_the_first_failure(tmp_path, ran, monkeypatch):
    monkeypatch.setattr(system_info, "_powercfg", lambda *args: (0, POWERCFG_LIST.replace("High performance", "Other")
                                                                .replace("8c5e7fda", "00000000")))
    write_routines(routines.ROUTINES_FILE, [{**CODING, "steps": [CODING["steps"][0], CODING["steps"][2],
                                                                CODING["steps"][1]]}])
    max_, _ = agent(True)
    result = max_.handle("coding mode")
    assert not result.success
    assert result.message.startswith("coding mode stopped at step 2, switch the power plan to high performance: "
                                     "This PC doesn't have the high performance plan")
    assert result.message.endswith("I already did: open vs code.")
    assert ran == [("open", "vs code")]  # the terminal step never ran


def test_safe_routines_do_not_ask(tmp_path, ran):
    write_routines(routines.ROUTINES_FILE, [{"name": "quiet", "steps": [{"tool": "play_pause"}]}])
    max_, asked = agent()
    assert max_.handle("quiet mode").success
    assert asked.questions == [] and ran == [("key", "play_pause")]


def test_critical_steps_still_ask_on_their_own(tmp_path, ran, monkeypatch):
    from modules import tools
    monkeypatch.setattr(tools.system_control, "check_process", lambda name: None)
    monkeypatch.setattr(tools.system_control, "end_process", lambda name: Result(True, f"Ended {name}"))
    write_routines(routines.ROUTINES_FILE, [{"name": "cleanup", "steps": [
        {"tool": "open_app", "args": {"name": "notepad"}},
        {"tool": "end_process", "args": {"process_name": "discord.exe"}}]}])
    max_, asked = agent(True, False)
    result = max_.handle("cleanup")
    assert asked.questions[1] == "This needs extra care. Should I really end the process discord.exe?"
    assert "stopped at step 2" in result.message


def test_agent_can_run_a_routine(tmp_path, ran):
    write_routines(routines.ROUTINES_FILE, [{"name": "quiet", "steps": [{"tool": "play_pause"}]}])
    provider = FakeProvider([Reply(calls=[ToolCall("run_routine", {"name": "quiet"})]), Reply(text="Done.")])
    max_, asked = agent(True, provider=provider)
    max_.handle("put on my quiet routine and then tell me the time")
    assert asked.questions == ["Should I run quiet: pause or resume the music?"]
    assert ran == [("key", "play_pause")]


def test_list_routines(tmp_path):
    write_routines(routines.ROUTINES_FILE, [CODING, {"name": "broken", "steps": [{"tool": "nope"}]}])
    result = agent()[0].handle("what routines do i have")
    assert result.message == "You have 1 routine: coding mode. 1 couldn't be loaded; the details are printed."
    assert "Skipped: broken" in result.detail


def test_example_routines_are_valid():
    from pathlib import Path
    data = json.loads((Path(__file__).parent.parent / "routines.json.example").read_text(encoding="utf-8"))
    found, problems = routines.parse(data)
    assert problems == [] and len(found) == 3


# ---------- 8. activity recall ----------

NOW = datetime(2026, 11, 6, 15, 0)


def log_line(time, tool, args=None, success=True):
    return json.dumps({"time": time, "source": "local", "tool": tool, "args": args or {}, "success": success,
                       "message": "secret message text"})


@pytest.fixture
def history(tmp_path):
    path = tmp_path / "history.jsonl"
    path.write_text("\n".join([
        log_line("2026-11-05T09:00:00", "open_app", {"name": "chrome"}),
        log_line("2026-11-05T09:05:00", "open_app", {"name": "vs code"}),
        log_line("2026-11-05T10:00:00", "open_app", {"name": "chrome"}),
        log_line("2026-11-05T10:30:00", "play_music", {"query": "softly"}),
        log_line("2026-11-05T11:00:00", "remember", {"fact": "my bank pin hint is cats"}),
        log_line("2026-11-05T11:05:00", "system_status"),
        log_line("2026-11-05T12:00:00", "close_app", {"name": "chrome"}),
        log_line("2026-11-05T12:30:00", "open_app", {"name": "steam"}, success=False),
        log_line("2026-11-06T09:00:00", "open_app", {"name": "discord"}),
        "not json",
    ]), encoding="utf-8")
    return path


def test_what_did_i_do_yesterday(history):
    result = activity.activity_summary(history, "yesterday", now=NOW)
    assert result.message == ("Yesterday I did 6 things for you: opened chrome twice and vs code, closed chrome, "
                              "played 1 song and changed your memories once.")
    assert "bank" not in result.message


def test_most_opened_apps_this_week(history):
    assert activity.activity_summary(history, "this week", "apps", now=NOW).message == \
        "Most opened this week: chrome twice, vs code once and discord once."


def test_songs_today_when_none(history):
    assert activity.activity_summary(history, "today", "songs", now=NOW).message == "I didn't play any songs today."


def test_nothing_logged(tmp_path):
    assert activity.activity_summary(tmp_path / "none.jsonl", "today", now=NOW).message == \
        "I didn't do anything for you today."


def test_period_range():
    start, end = activity.period_range("this week", NOW)  # Friday
    assert start == datetime(2026, 11, 2) and end == NOW
    start, end = activity.period_range("last week", NOW)
    assert start == datetime(2026, 10, 26) and end == datetime(2026, 11, 2)


def test_activity_tool_reads_the_action_log(monkeypatch, history):
    monkeypatch.setattr(actions, "ACTIONS_LOG", str(history))
    result = agent()[0].handle("which apps did i open most this week")
    assert result.success and "chrome" in result.message


@pytest.mark.parametrize("raw, name", [
    ("MicrosoftEdgeAutoLaunch_1F8C9CAA32C66112118302A9123F9587", "Microsoft Edge"),
    ("GoogleChromeAutoLaunch_D3402AF9D5146512D32811827D0FDCBD", "Google Chrome"),
    ("OneDrive", "OneDrive"), ("Steam.lnk", "Steam"), ("RtkAudUService", "RtkAudUService"),
])
def test_startup_names(raw, name):
    assert system_info._pretty(raw) == name


def test_startup_report_groups_browser_profiles(monkeypatch):
    monkeypatch.setattr(system_info, "startup_apps", lambda: [("Microsoft Edge", "registry", True)] * 3
                        + [("OneDrive", "registry", True)])
    assert system_info.startup_report().message == \
        "2 apps start with Windows: Microsoft Edge (3 entries), OneDrive."


def test_reminder_steps_read_naturally():
    reminder = tool_registry.get("set_reminder")
    assert reminder.describe({"message": "break is over", "minutes": 10.0}) == "set a reminder in 10 minutes: break is over"
    assert reminder.describe({"message": "stretch", "at_time": "17:30"}) == "set a reminder for 17:30: stretch"


# ---------- third voice test (6 Nov, 01:28-01:50) ----------

@pytest.mark.parametrize("said, command", [
    ("why is my laptop so slow", "health report"),
    ("how long has been my pc on", "uptime"),
    ("switch to ip performance", "set power plan"),
    ("what proteins do i have", "list routines"),
    ("are alert song", "alerts status"),
    ("undo the alerts", "undo"), ("ok and do them", "undo"),
    ("which apps i did open most", "activity apps"),
    ("pause the song pause", "play pause"),
    ("where do i keep my code", "recall"), ("what music do i like", "recall"), ("music do i like", "recall"),
    ("when was i born", "recall"), ("what kind of songs do i like", "recall"),
    ("open my project folder", "open folder"), ("open d drive coding arc", "open folder"),
    ("open chrome", "open"), ("what did i do today", "activity"),
])
def test_third_voice_test_phrases(said, command):
    from desktop_agent import fix_misheard, needs_agent
    max_, _ = agent()
    text = fix_misheard(max_.normalize(said))
    assert max_.match(text)[0].name == command
    assert not needs_agent(text)


def test_so_still_joins_two_requests():
    from desktop_agent import needs_agent
    assert needs_agent("close chrome so i can play games")


@pytest.mark.parametrize("said, fact", [
    ("where do I keep my code", "My project folder is D:\\Code"),
    ("what music do I like", "I love Punjabi songs from Karan Aujla"),
    ("when was I born", "My birthday is 13 June"),
])
def test_recall_questions_answer_locally(said, fact):
    for f in ("My project folder is D:\\Code", "I love Punjabi songs from Karan Aujla", "My birthday is 13 June"):
        store().remember(f)
    provider = FakeProvider()
    max_, _ = agent(provider=provider)
    result = max_.handle(said)
    assert result.message == memory_engine.as_you(fact)[:1].upper() + memory_engine.as_you(fact)[1:] + "."
    assert provider.fake_session.sent == []


def test_a_related_word_alone_is_not_a_match():
    store().remember("My project folder is D:\\Code")
    assert store().find("where do I keep my food") == []


def test_remember_with_nothing_after_it():
    max_, _ = agent()
    assert max_.handle("remember my").message.startswith("I didn't catch what to remember")
    assert store().count() == 0


def test_open_folder_from_memory(monkeypatch):
    from modules import system_control
    opened = []
    monkeypatch.setattr(system_control, "spoken_path",
                        lambda said: "D:\\Coding Arc" if said and "coding arc" in said.lower() else None)
    monkeypatch.setattr(system_control, "open_folder", lambda path: opened.append(path) or Result(True, f"Opening {path}"))
    store().remember("My project folder is D drive coding arc")
    max_, asked = agent()
    assert max_.handle("open my project folder").message == "Opening D:\\Coding Arc"
    assert max_.handle("open d drive coding arc").success
    assert opened == ["D:\\Coding Arc", "D:\\Coding Arc"] and asked.questions == []


def test_open_folder_that_is_not_known(monkeypatch):
    max_, _ = agent()
    assert "I don't know where your project folder is" in max_.handle("open my project folder").message


def test_open_folder_saved_but_missing(monkeypatch):
    store().remember("My project folder is Q drive nowhere")
    max_, _ = agent()
    assert "can't find that folder" in max_.handle("open my project folder").message


def test_spoken_path(tmp_path, monkeypatch):
    from modules import system_control
    (tmp_path / "Coding Arc" / "GitHub Repos").mkdir(parents=True)
    drive = str(tmp_path)
    monkeypatch.setattr(system_control, "SPOKEN_DRIVE",
                        __import__("re").compile(r"^(?:the )?(?P<letter>x)(?: drive|:)\s*(?P<rest>.*)$"))
    real_isdir = system_control.os.path.isdir
    monkeypatch.setattr(system_control.os.path, "isdir",
                        lambda p: real_isdir(p.replace("X:\\", drive + "\\", 1) if p.startswith("X:") else p))
    real_listdir = system_control.os.listdir
    monkeypatch.setattr(system_control.os, "listdir",
                        lambda p: real_listdir(p.replace("X:\\", drive + "\\", 1) if p.startswith("X:") else p))
    assert system_control.spoken_path("x drive coding arc") == "X:\\Coding Arc"
    assert system_control.spoken_path("x drive coding art github repos") == "X:\\Coding Arc\\GitHub Repos"
    assert system_control.spoken_path("x drive cooking") is None
    assert system_control.spoken_path("coding arc") is None


def test_close_terminal_closes_windows_not_max(monkeypatch):
    from modules import system_control
    closed = []
    monkeypatch.setattr(system_control, "close_terminals", lambda: closed.append(True) or Result(True, "Closed 2"))
    max_, asked = agent(True)
    assert max_.handle("close terminal").message == "Closed 2"
    assert asked.questions == ["Close the terminal windows? Not the one I'm running in."]
    assert closed == [True]


def test_close_terminals_skips_its_own_console(monkeypatch):
    from modules import system_control
    windows = [(1, "Windows PowerShell"), (2, "Command Prompt")]
    monkeypatch.setattr(system_control, "console_windows", lambda: list(windows))
    monkeypatch.setattr(system_control, "_own_console", lambda: 99)
    monkeypatch.setattr(system_control, "_post_close", lambda hwnds: windows.clear())
    assert system_control.close_terminals().message == "Closed 2 terminal windows, but not the one I run in."


@pytest.mark.parametrize("said", ["forget memory {id}", "forget number {id}", "forget {id}"])
def test_forget_by_number(said):
    fact, _ = store().remember("I like lo-fi")
    store().remember("My car is white")
    max_, asked = agent(True)
    max_.handle(said.format(id=fact.id))
    assert asked.questions == ["Should I forget that you like lo-fi?"]
    assert [f.text for f in store().all()] == ["My car is white"]


def test_forget_a_number_that_does_not_exist():
    assert agent(True)[0].handle("forget memory 99").message == "I don't have a memory number 99."
