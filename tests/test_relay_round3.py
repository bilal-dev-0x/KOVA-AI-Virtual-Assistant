import time

import pytest

import desktop_agent
from desktop_agent import Agent, normalize, parse_reminder
from modules import reminders, tools
from modules.result import Result


# ---------- 1. reminders said as "set a reminder ..." ----------

@pytest.mark.parametrize("text, message, seconds", [
    ("set a reminder for 10 seconds", "timer", 10),
    ("set a reminder of 10 second", "timer", 10),
    ("set a reminder for 5 minutes to call mom", "call mom", 300),
    ("set a reminder to stretch in 20 minutes", "stretch", 1200),
])
def test_set_a_reminder_phrases(text, message, seconds):
    assert parse_reminder(text) == (message, seconds)


# ---------- 2. short timers count from the end of Max's answer ----------

def test_short_timer_starts_after_the_answer():
    with reminders.hold_short_timers():
        reminder = reminders.add("tea", 10)
        time.sleep(0.2)
        assert not reminder.timer.is_alive()   # still waiting for Max to finish talking
    assert reminder.timer.is_alive()
    assert reminder.due - time.time() > 9.5


def test_long_reminder_starts_at_once():
    with reminders.hold_short_timers():
        reminder = reminders.add("stretch", 600)
        assert reminder.timer.is_alive()


def test_held_timer_can_still_be_cancelled():
    with reminders.hold_short_timers():
        reminders.add("tea", 10)
        assert reminders.cancel() == 1
    assert reminders.pending() == []


# ---------- 3. file search ----------

@pytest.mark.parametrize("text, query", [
    ("search resident evil in a file explorer", "resident evil"),
    ("find my cv on my pc", "cv"),
    ("search led table in file explorer", "led table"),
])
def test_search_on_the_pc_uses_file_explorer(monkeypatch, text, query):
    from modules import tool_registry
    searched = []
    monkeypatch.setattr(tool_registry.get("search_files"), "func",
                        lambda query: searched.append(query) or Result(True, "Searching"))
    Agent().handle(text)
    assert searched == [query]


def test_vague_pc_search_asks_what_to_find(monkeypatch):
    from modules import tool_registry
    searched = []
    monkeypatch.setattr(tool_registry.get("search_files"), "func",
                        lambda query: searched.append(query) or Result(True, "Searching"))
    agent = Agent()
    agent.ask = lambda question: "resident evil"
    agent.handle("search anything on my pc")
    assert searched == ["resident evil"]


# ---------- 4. check logs ----------

def test_recent_problems_reads_todays_warnings(tmp_path, monkeypatch):
    from datetime import datetime
    from modules import logger
    today = datetime.now().strftime("%Y-%m-%d")
    (tmp_path / "kova.log").write_text(
        f"2020-01-01 10:00:00,000 WARNING old: problem\n"
        f"{today} 10:00:00,000 INFO fine\n"
        f"{today} 10:01:00,000 WARNING Natural voice failed (SocketTimeoutError), using the fallback\n",
        encoding="utf-8")
    monkeypatch.setattr(logger, "LOG_DIR", str(tmp_path))
    result = tools.recent_problems()
    assert result.message.startswith("I logged 1 problems today")
    assert "Natural voice failed" in result.message


@pytest.mark.parametrize("text", ["check logs", "check locks", "check the logs", "show errors"])
def test_check_logs_is_local(text):
    agent = Agent()
    assert agent.match(agent.normalize(text))[0].name == "check logs"


# ---------- 5. GPU temperature ----------

def test_gpu_report_adds_the_temperature(monkeypatch):
    from modules import performance_monitor
    monkeypatch.setattr(performance_monitor, "_gpu_samples", lambda: [])
    monkeypatch.setattr(performance_monitor, "gpu_temperature", lambda: ("Quadro P620", 46))
    assert performance_monitor.gpu_report().message == "GPU usage is 0 percent. The Quadro P620 is at 46 degrees."


# ---------- 6. the song, and repeated phrases ----------

# "close the song now" closes its YouTube tab since the Day 3 voice test (test_day3_fixes.py)
@pytest.mark.parametrize("text", ["stop the music", "pause the song pause the song", "end this video"])
def test_stopping_the_song_pauses_it(text):
    agent = Agent()
    assert agent.match(agent.normalize(text))[0].name == "play pause"


@pytest.mark.parametrize("raw, expected", [
    ("pause the song pause the song", "pause the song"),
    ("close yourself close yourself", "close yourself"),
    ("no no no", "no"),
    ("open chrome and open chrome tabs", "open chrome and open chrome tabs"),
])
def test_repeats_collapse(raw, expected):
    assert normalize(raw) == expected


# ---------- 7. new tab ----------

@pytest.mark.parametrize("text", ["open tab in google", "open a new tab", "new tab"])
def test_new_tab(monkeypatch, text):
    opened = []
    monkeypatch.setattr(desktop_agent.system_control, "open_url", lambda url, config=None: opened.append(url))
    Agent().handle(text)
    assert opened == ["https://www.google.com/"]


# ---------- 8. restart yourself ----------

@pytest.mark.parametrize("text", ["restart yourself", "max restart yourself please", "reload yourself"])
def test_restart_yourself(text):
    result = Agent({"WAKE_WORDS": ["max"]}).handle(text)
    assert result.stop and result.restart


def test_restart_an_app_is_not_restarting_max(monkeypatch):
    agent = Agent()
    assert agent.match(agent.normalize("restart chrome"))[0].name == "restart"


def test_main_starts_again_after_restart(monkeypatch):
    import main
    runs = []

    def fake_voice(agent):
        runs.append(agent)
        return Result(True, "Restarting.", stop=True, restart=len(runs) == 1)

    monkeypatch.setattr(main, "run_voice", fake_voice)
    monkeypatch.setattr(main, "load_config", lambda: {"AGENT_TRACE": False})
    monkeypatch.setattr(main, "single_instance", lambda: True)
    monkeypatch.setattr("sys.argv", ["main.py"])
    main.main()
    assert len(runs) == 2 and runs[0] is not runs[1]


# ---------- 9. "Close what?" -> "yourself" ----------

def test_close_what_yourself_exits():
    agent = Agent()
    agent.ask = lambda question: "yourself"
    assert agent.handle("close").stop


# ---------- which window to close ----------

CHROME_WINDOWS = [(1, "YouTube - Google Chrome"), (2, "Gmail - Inbox - Google Chrome")]


@pytest.fixture
def two_chrome_windows(monkeypatch):
    from modules import system_control
    monkeypatch.setattr(system_control, "app_windows", lambda names: list(CHROME_WINDOWS))
    monkeypatch.setattr(system_control, "check_app", lambda name, must_be_running=False: (name, None))
    closed = []
    monkeypatch.setattr(system_control, "close_app",
                        lambda name, window="": closed.append((name, window)) or Result(True, "Closed"))
    return closed


@pytest.mark.parametrize("answer, window", [
    ("the second one", "Gmail - Inbox"),
    ("youtube", "YouTube"),
    ("the one with gmail", "Gmail - Inbox"),
    ("one", "YouTube"),
])
def test_max_asks_which_window(two_chrome_windows, answer, window):
    agent = Agent()
    questions = []
    agent.ask = lambda q: questions.append(q) or answer
    agent.confirm = lambda q: pytest.fail("picking a window is the answer, it isn't asked twice")
    agent.handle("close chrome")
    assert questions == ["chrome has 2 windows: one, YouTube; two, Gmail - Inbox. Which one should I close, or all of them?"]
    assert two_chrome_windows == [("chrome", window)]


def test_all_windows_closes_the_app(two_chrome_windows):
    agent = Agent()
    agent.ask = lambda q: "close all of them"
    agent.handle("close chrome")
    assert two_chrome_windows == [("chrome", "")]


@pytest.mark.parametrize("answer", ["no", "never mind", None, "something completely different"])
def test_unclear_window_answer_closes_nothing(two_chrome_windows, answer):
    agent = Agent()
    agent.ask = lambda q: answer
    result = agent.handle("close chrome")
    assert not result.success
    assert two_chrome_windows == []


def test_one_window_asks_the_normal_question(monkeypatch, two_chrome_windows):
    from modules import system_control
    monkeypatch.setattr(system_control, "app_windows", lambda names: [(1, "YouTube - Google Chrome")])
    questions = []
    agent = Agent(confirm=lambda q: questions.append(q) or True)
    agent.ask = lambda q: pytest.fail("one window: nothing to choose")
    agent.handle("close chrome")
    assert questions == []
    assert two_chrome_windows == [("chrome", "")]


def test_ai_can_list_and_close_one_window(two_chrome_windows):
    assert tools.list_windows(name="chrome").message == "chrome has 2 windows: YouTube; Gmail - Inbox."
    tools.close_app(name="chrome", window="Gmail")
    assert two_chrome_windows == [("chrome", "Gmail")]


def test_task_manager_tab(monkeypatch):
    from modules import tool_registry
    monkeypatch.setattr(tool_registry.get("open_app"), "func", lambda name: Result(True, f"Opening {name}"))
    result = Agent().handle("open performance tab in task manager")
    assert result.message == "Opening Task Manager. Click the Performance tab on the left."


# ---------- round 4 ----------

def test_second_max_refuses_to_start(monkeypatch, capsys):
    import main
    monkeypatch.setattr(main, "single_instance", lambda: False)
    monkeypatch.setattr(main, "run_voice", lambda agent: pytest.fail("a second Max must not listen"))
    monkeypatch.setattr("sys.argv", ["main.py"])
    main.main()
    assert "already running" in capsys.readouterr().out


def test_text_mode_may_run_next_to_voice_mode(monkeypatch):
    import main
    monkeypatch.setattr(main, "single_instance", lambda: pytest.fail("text mode doesn't listen, so it isn't locked"))
    monkeypatch.setattr(main, "run_text", lambda agent: None)
    monkeypatch.setattr(main, "load_config", lambda: {"AGENT_TRACE": False})
    monkeypatch.setattr("sys.argv", ["main.py", "--text"])
    main.main()


def test_admin_app_is_refused_before_trying(monkeypatch):
    from modules import system_control
    monkeypatch.setattr(system_control, "_is_running", lambda entry, names=None: True)
    monkeypatch.setattr(system_control, "_needs_admin", lambda names: True)
    key, problem = system_control.check_app("task manager", must_be_running=True)
    assert key is None and "administrator" in problem.message


@pytest.mark.parametrize("text", ["open one more window of brave browser", "new window of chrome",
                                  "open another window of brave", "open edge in a new window"])
def test_new_window_phrases(text):
    agent = Agent()
    assert agent.match(agent.normalize(text))[0].name == "new window"


def test_new_browser_window_uses_the_exe(monkeypatch):
    from modules import system_control
    started = []
    monkeypatch.setattr(system_control, "browser_exe", lambda process: rf"C:\Browsers\{process}")
    monkeypatch.setattr(system_control.subprocess, "Popen", lambda args: started.append(args))
    assert Agent().handle("open one more window of brave browser").message == "Opening a new brave window"
    assert started == [[r"C:\Browsers\brave.exe", "--new-window"]]


def test_new_window_of_other_apps_opens_them_again(monkeypatch):
    from modules import system_control
    opened = []
    monkeypatch.setattr(system_control, "open_target",
                        lambda name, config=None: opened.append(name) or Result(True, f"Opening {name}"))
    assert Agent().handle("new window of notepad").message.startswith("Opening notepad again")
    assert opened == ["notepad"]


@pytest.mark.parametrize("text, url", [
    ("search x in brave browser", "https://www.google.com/search?q=x"),
    ("search cats on youtube with chrome", "https://www.youtube.com/results?search_query=cats"),
    ("search discord com in edge", "https://www.discord.com/"),
])
def test_search_in_a_named_browser(monkeypatch, text, url):
    from modules import system_control
    started = []
    monkeypatch.setattr(system_control, "browser_exe", lambda process: rf"C:\Browsers\{process}")
    monkeypatch.setattr(system_control.subprocess, "Popen", lambda args: started.append(args))
    monkeypatch.setattr(system_control, "open_url", lambda u, config=None: pytest.fail("used the default browser"))
    Agent().handle(text)
    assert started[0][1] == url


def test_freed_ram_wording_and_background_ram(monkeypatch):
    from modules import system_control
    memory = iter([937, 200])  # before closing, and what the background processes still use
    monkeypatch.setattr(system_control, "app_memory_mb", lambda name: next(memory))
    monkeypatch.setattr(system_control, "close_app", lambda name: Result(
        True, "Closed brave. It's still running in the background; say force close brave to end that too."))
    result = tools.close_app(name="brave")
    assert result.message == ("Closed brave, freeing about 737 MB of RAM. It's still running in the background; "
                              "say force close brave to end that too.")
