"""Fixes for what the Day 3 voice test found (5 Nov 2026)."""
import pytest

import main
from desktop_agent import Agent, needs_agent
from modules import memory_engine, system_control, tool_registry
from modules.result import Result

CONFIG = {"ASSISTANT_NAME": "Max", "WAKE_WORDS": ["max"]}


class Asked:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.questions = []

    def __call__(self, question):
        self.questions.append(question)
        return self.answers.pop(0) if self.answers else False


def agent(*answers, said=()):
    """Max with scripted yes/no answers, and scripted answers to open questions."""
    asked = Asked(*answers)
    max_ = Agent(CONFIG, confirm=asked, provider=None)
    replies = list(said)
    max_.ask = lambda question: (asked.questions.append(question), replies.pop(0) if replies else None)[1]
    return max_, asked


def store():
    return memory_engine.get_memory()


# ---------- 2. "pause it" ----------

@pytest.mark.parametrize("said", ["pause it", "stop it", "pause this", "resume it", "play it again",
                                  "Max, pause that"])
def test_pause_it(said):
    max_, _ = agent()
    assert max_.match(max_.normalize(said))[0].name == "play pause"


# ---------- 3. "how is my cpu and ram" stays local ----------

@pytest.mark.parametrize("said", ["how is my cpu and ram", "hows my cpu and ram", "what is my ram and cpu usage",
                                  "check my cpu and ram", "cpu and ram", "how much cpu and ram am i using"])
def test_cpu_and_ram_stays_local(said):
    max_, _ = agent()
    text = max_.normalize(said)
    assert not needs_agent(text)
    assert max_.match(text)[0].name == "cpu and ram"


@pytest.mark.parametrize("said", ["close whatever uses most cpu and ram", "check cpu and ram and close chrome"])
def test_real_multi_part_requests_still_go_to_the_agent(said):
    assert needs_agent(said)


# ---------- 4. "close lofi music": a window by what it shows ----------

LOFI = "lofi hip hop radio - beats to relax/study to - YouTube - Google Chrome"
SOFTLY = "Softly - Karan Aujla - YouTube - Google Chrome"


@pytest.mark.parametrize("title, query, found", [
    (LOFI, "lofi music", True),
    (LOFI, "lo fi", False),            # "lo" and "fi" alone are too short to match inside words
    (SOFTLY, "softly by karan aujla", True),
    (SOFTLY, "softly by diljit", False),
    ("Lo-Fi Beats - YouTube", "lofi", True),
    (SOFTLY, "the song", False),       # nothing left to look for
])
def test_title_has(title, query, found):
    assert system_control.title_has(title, system_control.title_words(query)) is found


def test_window_titled_searches_running_apps(monkeypatch):
    monkeypatch.setattr(system_control, "_running_names", lambda: {"chrome.exe", "notepad.exe"})
    windows = {"chrome.exe": [(1, "Gmail - Google Chrome"), (2, SOFTLY)], "notepad.exe": [(3, "notes - Notepad")]}
    monkeypatch.setattr(system_control, "app_windows",
                        lambda procs: [w for p in procs for w in windows.get(p.lower(), [])])
    assert system_control.window_titled("softly by karan aujla") == ("chrome", SOFTLY)
    assert system_control.window_titled("notes") == ("notepad", "notes - Notepad")
    assert system_control.window_titled("lofi music") is None


@pytest.fixture
def screen(monkeypatch):
    """Chrome shows Softly, Notepad shows notes.txt; records what was closed."""
    closed = []
    monkeypatch.setattr(system_control, "check_app", lambda name, must_be_running=False: (
        (name, None) if name in ("chrome", "notepad") else (None, Result(False, f"I don't know an app called {name}."))))
    found = {"softly by karan aujla": ("chrome", SOFTLY), "notes": ("notepad", "notes.txt - Notepad")}
    monkeypatch.setattr(system_control, "window_titled", lambda query: found.get(query))
    monkeypatch.setattr(system_control, "close_tab",
                        lambda name, title: closed.append(("tab", name, title)) or Result(True, "Closed the tab"))
    monkeypatch.setattr(system_control, "close_app",
                        lambda name, window="": closed.append(("window", name, window)) or Result(True, "Closed"))
    return closed


def test_close_a_song_closes_its_browser_tab(screen):
    max_, asked = agent(True)
    assert max_.handle("Max, close Softly by Karan Aujla").success
    assert asked.questions == ["Close the Softly - Karan Aujla tab in chrome?"]
    assert screen == [("tab", "chrome", SOFTLY)]


def test_close_a_window_of_another_app_by_its_title(screen):
    max_, asked = agent(True)
    max_.handle("close my notes")
    assert asked.questions == ["Close the notepad window notes.txt?"]
    assert screen == [("window", "notepad", "notes.txt - Notepad")]


def test_close_by_title_can_be_declined(screen):
    max_, _ = agent(False)
    assert max_.handle("close softly by karan aujla").message == "Okay, cancelled."
    assert screen == []


def test_close_something_that_is_nowhere(screen):
    max_, asked = agent(True)
    assert max_.handle("close lofi music").message == "I don't know an app called lofi music."
    assert asked.questions == [] and screen == []


def test_close_tab_only_presses_keys_in_the_right_window(monkeypatch):
    pressed = []
    monkeypatch.setattr(system_control, "app_windows", lambda procs: [(7, SOFTLY)])
    monkeypatch.setattr(system_control, "_bring_to_front", lambda hwnd: False)
    monkeypatch.setattr(system_control, "_press_ctrl_w", lambda: pressed.append(True))
    result = system_control.close_tab("chrome", SOFTLY)
    assert not result.success and "didn't close anything" in result.message
    assert pressed == []


def test_close_tab_checks_the_tab_went_away(monkeypatch):
    titles = {7: SOFTLY}
    monkeypatch.setattr(system_control, "app_windows", lambda procs: [(7, SOFTLY)])
    monkeypatch.setattr(system_control, "_bring_to_front", lambda hwnd: True)
    monkeypatch.setattr(system_control, "_press_ctrl_w", lambda: titles.update({7: "Gmail - Google Chrome"}))
    monkeypatch.setattr(system_control, "_window_title", lambda hwnd: titles[hwnd])
    assert system_control.close_tab("chrome", SOFTLY).message == "Closed the Softly - Karan Aujla tab in chrome"


# ---------- 5. the approval question names the window ----------

def test_approval_names_the_window():
    close = tool_registry.get("close_app")
    assert close.describe({"name": "chrome", "window": SOFTLY}) == "close the chrome window Softly - Karan Aujla"
    assert close.describe({"name": "chrome"}) == "close chrome"
    tab = tool_registry.get("close_tab")
    assert tab.level == "controlled"
    assert tab.describe({"name": "chrome", "title": "Softly"}) == "close the Softly tab in chrome"


def test_close_tab_refuses_apps_that_are_not_browsers():
    assert "isn't a browser" in tool_registry.get("close_tab").check(name="notepad", title="notes").message


# ---------- 6. "forget that" with nothing said about a memory this run ----------

def test_forget_that_asks_which_one():
    store().remember("I like lo-fi")
    store().remember("My project folder is D drive")
    max_, asked = agent(True, said=["my project folder"])
    result = max_.handle("forget that")
    assert asked.questions == ["Which one should I forget?", "Should I forget that your project folder is D drive?"]
    assert result.message == "Okay, I forgot that your project folder is D drive."
    assert [f.text for f in store().all()] == ["I like lo-fi"]


def test_forget_that_answer_can_repeat_the_command():
    store().remember("I like lo-fi")
    store().remember("My project folder is D drive")
    max_, _ = agent(True, said=["forget the lo-fi one"])
    max_.handle("forget that")
    assert [f.text for f in store().all()] == ["My project folder is D drive"]


@pytest.mark.parametrize("answer", [None, "never mind", "ok bye", "nothing"])
def test_forget_that_answer_can_drop_it(answer):
    store().remember("I like lo-fi")
    store().remember("My project folder is D drive")
    max_, _ = agent(True, said=[answer])
    assert max_.handle("forget that").message == "Okay, never mind."
    assert store().count() == 2


def test_forget_that_with_one_memory_means_that_one():
    store().remember("I like lo-fi")
    max_, asked = agent(True)
    max_.handle("forget that")
    assert asked.questions == ["Should I forget that you like lo-fi?"]
    assert store().count() == 0


# ---------- 7. "forget X and Y" ----------

def test_forget_two_things_at_once_and_undo_both():
    store().remember("My project folder is D drive")
    store().remember("I am a Python learner")
    store().remember("I like lo-fi")
    max_, asked = agent(True)
    result = max_.handle("forget my project folder and I am a Python learner")
    assert asked.questions == ["Should I forget that your project folder is D drive, and that you are a Python learner?"]
    assert result.message == "Okay, I forgot that your project folder is D drive, and that you are a Python learner."
    assert [f.text for f in store().all()] == ["I like lo-fi"]
    assert max_.handle("undo").success
    assert store().count() == 3


def test_forget_one_fact_that_has_and_in_it():
    store().remember("I like rock and roll")
    store().remember("My project folder is D drive")
    max_, asked = agent(True)
    max_.handle("forget I like rock and roll")
    assert asked.questions == ["Should I forget that you like rock and roll?"]
    assert [f.text for f in store().all()] == ["My project folder is D drive"]


def test_forget_two_things_when_one_is_unknown():
    store().remember("My project folder is D drive")
    store().remember("I like lo-fi")
    max_, _ = agent(True)
    result = max_.handle("forget my project folder and lo-fi and my car")
    assert result.message.endswith("I had nothing saved about your car.")
    assert store().count() == 0


# ---------- 8. answers to questions ----------

@pytest.mark.parametrize("answer, question, yes", [
    ("forget that", "Should I forget that your project folder is D drive?", True),
    ("yes forget it", "Should I forget that you like lo-fi?", True),
    ("don't forget it", "Should I forget that you like lo-fi?", False),
    ("delete it", "Should I forget that you like lo-fi?", False),   # "delete" isn't in the question
    ("save it", "Should I save this to my memory: I like lo-fi?", True),
    ("forget it", "Should I close chrome?", False),
])
def test_repeating_the_action_is_a_yes(answer, question, yes):
    assert main.is_yes(answer, question) is yes


@pytest.mark.parametrize("answer", ["ok bye", "bye", "never mind", "nothing", "cancel", "no thanks", "nevermind"])
def test_dropping_a_question(answer):
    max_, _ = agent(said=[answer])
    assert max_.handle("search").message == "Okay, never mind."
    max_, _ = agent(said=[answer])
    assert max_.handle("close").message == "Okay, never mind."


# ---------- second voice test (22:01-22:09) ----------

@pytest.mark.parametrize("title, said", [
    ("(30) Excuses (Official Video) | AP Dhillon | Gurinder Gill | Intense - YouTube - Google Chrome", "Excuses"),
    (SOFTLY, "Softly - Karan Aujla"),
    ("notes.txt - Notepad", "notes.txt"),
    ("(Official Video) - YouTube - Google Chrome", "(Official Video)"),  # nothing else to say
    ("SOFTLY KARAN AUJLA (Official Video) - YouTube - Google Chrome", "Softly Karan Aujla"),
    ("Mix - AP Dhillon - YouTube - Google Chrome", "Mix - AP Dhillon"),  # "AP" kept: not all capitals
])
def test_spoken_title(title, said):
    assert system_control.spoken_title(title) == said


EXCUSES = "(30) Excuses (Official Video) | AP Dhillon | Gurinder Gill | Intense - YouTube - Google Chrome"


@pytest.fixture
def youtube_tab(monkeypatch):
    closed = []
    monkeypatch.setattr(system_control, "window_titled",
                        lambda query: ("chrome", EXCUSES) if query == "youtube" else None)
    monkeypatch.setattr(system_control, "close_tab",
                        lambda name, title: closed.append((name, title)) or Result(True, "Closed the Excuses tab"))
    return closed


@pytest.mark.parametrize("said", ["close the song", "close this video", "close the music", "Max, close the song"])
def test_close_the_song_closes_its_youtube_tab(youtube_tab, said):
    max_, asked = agent(True)
    assert max_.handle(said).success
    assert asked.questions == ["Close the Excuses tab in chrome?"]
    assert youtube_tab == [("chrome", EXCUSES)]


def test_close_the_song_without_a_tab_pauses(monkeypatch):
    monkeypatch.setattr(system_control, "window_titled", lambda query: None)
    max_, _ = agent()
    assert max_.match("close the song")[0].name == "close song"
    assert max_.match("stop the song")[0].name == "play pause"


@pytest.mark.parametrize("heard", ["opposite", "positive", "Max opposite"])
def test_misheard_pause_it(heard, monkeypatch):
    from modules import tools
    paused = []
    monkeypatch.setattr(tools.media_keys, "press", lambda key, times=1: paused.append(key))
    max_, _ = agent()
    max_.handle(heard)
    assert paused == ["play_pause"]


def test_forgot_that_is_forget_that():
    store().remember("I like lo-fi")
    max_, asked = agent(True)
    max_.handle("forgot that")
    assert asked.questions == ["Should I forget that you like lo-fi?"]


def test_forget_that_after_hearing_the_list_asks_which():
    max_, asked = agent(True, said=["the project folder"])
    max_.handle("remember I like lo-fi")
    max_.handle("remember my project folder is D drive")
    max_.handle("what do you know about me")
    max_.handle("forget that")
    assert asked.questions == ["Which one should I forget?", "Should I forget that your project folder is D drive?"]


@pytest.mark.parametrize("answer", ["all", "everything", "yes forget all", "all of them", "both"])
def test_forget_that_answered_with_all(answer):
    store().remember("I like lo-fi")
    store().remember("My project folder is D drive")
    max_, asked = agent(True, True, said=[answer])
    max_.handle("forget that")
    assert asked.questions[1] == "Should I forget everything I know about you? That's 2 things."
    assert store().count() == 0


def test_offline_voice_gives_up_fast_and_backs_off(monkeypatch):
    from modules import voice_handler
    monkeypatch.setattr(voice_handler, "_edge_failures", 0)
    monkeypatch.setattr(voice_handler, "_edge_down_until", 0.0)
    monkeypatch.setattr(voice_handler, "_host_resolves", lambda host: False)
    waits = []
    for _ in range(3):
        voice_handler._edge_down_until = 0.0
        start = __import__("time").time()
        assert voice_handler._edge_mp3("hi", "unused.mp3") is False
        waits.append(round(voice_handler._edge_down_until - start))
    assert waits == [120, 240, 480]
