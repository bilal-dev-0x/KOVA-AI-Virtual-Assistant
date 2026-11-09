import pytest

import desktop_agent
from desktop_agent import Agent, normalize
from modules.result import Result


@pytest.fixture
def agent():
    # No Gemini key: KOVA must still work for local commands
    return Agent({"USER_TITLE": "Commander"})


@pytest.mark.parametrize("raw, expected", [
    ("Hey KOVA, can you open Chrome please!", "open chrome"),
    ("KOVA what's the time?", "whats the time"),
    ("  PLAY   Pasoori ", "play pasoori"),
    ("kova", ""),
])
def test_normalize(raw, expected):
    assert normalize(raw) == expected


class GeminiMustNotBeUsed:
    def session(self, system_prompt, tools):
        pytest.fail("went to Gemini")


MAX_CONFIG = {"ASSISTANT_NAME": "Max", "WAKE_WORDS": ["max", "imax", "macs", "maks", "mac"]}


@pytest.mark.parametrize("raw, expected", [
    ("Hey Max, open Chrome", "open chrome"),
    ("macs what time is it", "what time is it"),
    ("max", ""),
])
def test_normalize_with_configured_name(raw, expected):
    assert Agent(MAX_CONFIG).normalize(raw) == expected


@pytest.mark.parametrize("heard, after", [
    ("Max open notepad", " open notepad"),
    ("hey Macs", ""),
    ("okay max what is the time", " what is the time"),
    ("IMAX", ""),
    ("IMAX open notepad", " open notepad"),
])
def test_wake_word_detected(heard, after):
    assert Agent(MAX_CONFIG).find_wake_word(heard) == after


@pytest.mark.parametrize("heard", ["Goa", "High Court", "the maximum speed", "open chrome"])
def test_wake_word_not_detected(heard):
    assert Agent(MAX_CONFIG).find_wake_word(heard) is None


def test_stop_uses_configured_name():
    agent = Agent(MAX_CONFIG)
    assert agent.handle("stop max").stop is True


@pytest.mark.parametrize("text, command, arg", [
    ("open chrome", "open", "chrome"),
    ("launch vs code", "open", "vs code"),
    ("open youtube", "open", "youtube"),
    ("close notepad", "close", "notepad"),
    ("restart discord", "restart", "discord"),
    ("play pasoori", "play", "pasoori"),
    ("list apps", "list apps", None),
    ("what apps are running", "list apps", None),
    ("system status", "system status", None),
    ("status", "system status", None),
    ("whats my cpu usage", "cpu", None),
    ("how much ram am i using", "ram", None),
    ("how much disk space is left", "disk", None),
    ("battery", "battery", None),
    ("whats the date", "date", None),
    ("today date", "date", None),
    ("what time is it", "time", None),
    ("current time", "time", None),
    ("global news", "news", None),
    ("hello", "greet", None),
    ("help", "help", None),
    ("goodbye", "exit", None),
    ("exit", "exit", None),
    ("go to sleep", "sleep", None),
    ("thats all", "sleep", None),
])
def test_match(agent, text, command, arg):
    matched, matched_arg = agent.match(text)
    assert matched is not None, f"{text!r} matched nothing"
    assert matched.name == command
    assert matched_arg == arg


@pytest.mark.parametrize("text", [
    "explain how black holes work",
    "write a poem about rain",
    "hey whats the weather like",
])
def test_unknown_goes_to_fallback(agent, text):
    assert agent.match(text) == (None, None)


def test_fallback_without_gemini_key_does_not_crash(agent):
    result = agent.handle("explain how black holes work")
    assert result.success is False
    assert "Gemini isn't set up" in result.message


def test_fallback_uses_gemini_answer():
    from brain.base import Reply
    from tests.conftest import FakeProvider
    agent = Agent(provider=FakeProvider([Reply(text="Black holes are dense.")]))
    assert agent.handle("explain black holes").message == "Black holes are dense."


def test_gentle_close_command_does_not_ask(monkeypatch):
    monkeypatch.setattr(desktop_agent.system_control, "check_app",
                        lambda name, must_be_running=False: (name, None))
    closed = []
    monkeypatch.setattr(desktop_agent.system_control, "close_app",
                        lambda name: closed.append(name) or Result(True, "Closed"))

    questions = []
    no_agent = Agent(confirm=lambda q: questions.append(q) or False)
    result = no_agent.handle("close notepad")
    assert questions == []
    assert result.success
    assert closed == ["notepad"]


def test_controlled_command_asks_and_can_be_cancelled(monkeypatch):
    monkeypatch.setattr(desktop_agent.system_control, "check_force", lambda name: (name, None))
    forced = []
    monkeypatch.setattr(desktop_agent.system_control, "force_close",
                        lambda name: forced.append(name) or Result(True, "Force closed"))

    questions = []
    no_agent = Agent(confirm=lambda q: questions.append(q) or False)
    result = no_agent.handle("force close notepad")
    assert questions == ["Force close notepad? Anything unsaved in it will be lost."]
    assert forced == []
    assert result.message == "Okay, cancelled."

    yes_agent = Agent(confirm=lambda q: True)
    assert yes_agent.handle("force close notepad").success
    assert forced == ["notepad"]


def test_safe_command_does_not_ask(monkeypatch):
    monkeypatch.setattr(desktop_agent.system_control, "open_target", lambda name, config: Result(True, "Opening"))
    asked = []
    Agent(confirm=lambda q: asked.append(q) or True).handle("open notepad")
    assert asked == []


def test_handler_error_is_caught(agent, monkeypatch):
    def broken(name, config):
        raise RuntimeError("boom")
    monkeypatch.setattr(desktop_agent.system_control, "open_target", broken)
    result = agent.handle("open chrome")
    assert result.success is False
    assert "something went wrong" in result.message


def test_exit_stops(agent):
    assert agent.handle("goodbye").stop is True


def test_empty_input_answers(agent):
    assert agent.handle("kova").message == "Yes Commander?"


def test_help_lists_commands(agent):
    result = agent.handle("help")
    assert "open <app or website>" in result.detail
    assert "close <app>" in result.detail


def test_play_finds_song_with_partial_name(agent, monkeypatch):
    opened = []
    monkeypatch.setattr(desktop_agent.system_control, "open_url", lambda url, config=None: opened.append(url))
    result = agent.handle("play pasoori")
    assert result.message == "Playing Pasoori"
    assert opened == [desktop_agent.music_library.library["Pasoori - Ali Sethi x Shae Gill"]]


def test_play_tolerates_misheard_name(agent, monkeypatch):
    monkeypatch.setattr(desktop_agent.system_control, "open_url", lambda url, config=None: None)
    assert agent.handle("play beleiver").message == "Playing Believer"


def test_play_unknown_song_searches_youtube(agent, monkeypatch):
    opened = []
    monkeypatch.setattr(desktop_agent.system_control, "open_url", lambda url, config=None: opened.append(url))
    result = agent.handle("play some totally unknown track")
    assert "searched YouTube" in result.message
    assert opened[0].startswith("https://www.youtube.com/results?search_query=")


# ---------- fixes from the first voice test ----------

@pytest.mark.parametrize("raw, expected", [
    ("now open GitHub", "open github"),
    ("I said close chrome", "close chrome"),
    ("max max max", ""),
    ("okay then open steam", "open steam"),
])
def test_filler_words_removed(raw, expected):
    assert Agent(MAX_CONFIG).normalize(raw) == expected


def test_article_removed_from_target(agent):
    assert agent.match("open the spotify") == (agent.match("open spotify")[0], "spotify")


@pytest.mark.parametrize("text", ["open", "close", "close the", "restart"])
def test_missing_target_asks_instead_of_gemini(text, monkeypatch):
    asked = []
    agent = Agent(provider=GeminiMustNotBeUsed())
    agent.ask = lambda q: asked.append(q) or None
    result = agent.handle(text)
    assert asked and asked[0].endswith("what?")
    assert result.message == "Okay, never mind."


def test_answer_to_open_what_is_used(monkeypatch):
    opened = []
    monkeypatch.setattr(desktop_agent.system_control, "open_target",
                        lambda name, config: opened.append(name) or Result(True, "Opening"))
    agent = Agent()
    agent.ask = lambda q: "the notepad"
    agent.handle("open")
    assert opened == ["notepad"]


def test_unknown_app_is_rejected_before_confirming():
    asked = []
    result = Agent(confirm=lambda q: asked.append(q) or True).handle("close flux capacitor")
    assert asked == []
    assert "don't know an app" in result.message


def test_website_cannot_be_closed():
    asked = []
    result = Agent(confirm=lambda q: asked.append(q) or True).handle("close youtube")
    assert asked == []
    assert "website" in result.message


def test_app_not_running_is_not_confirmed(monkeypatch):
    monkeypatch.setattr(desktop_agent.system_control, "_running_names", lambda: set())
    asked = []
    result = Agent(confirm=lambda q: asked.append(q) or True).handle("close notepad")
    assert asked == []
    assert "not running" in result.message


@pytest.mark.parametrize("text, key", [
    ("volume up", "volume_up"),
    ("turn it up", "volume_up"),
    ("louder", "volume_up"),
    ("volume down", "volume_down"),
    ("lower the volume", "volume_down"),
    ("mute", "mute"),
    ("pause", "play_pause"),
    ("resume the music", "play_pause"),
    ("play", "play_pause"),
    ("next song", "next"),
    ("play next song", "next"),
    ("skip", "next"),
    ("previous track", "previous"),
])
def test_media_commands(text, key, agent, monkeypatch):
    pressed = []
    monkeypatch.setattr(desktop_agent.media_keys, "press", lambda k, times=1: pressed.append(k))
    assert agent.handle(text).success
    assert pressed == [key]


def test_play_song_still_works_after_media_commands(agent):
    assert agent.match("play believer")[0].name == "play"


@pytest.mark.parametrize("text", ["force close calculator", "close calculator anyway", "close calculator forcefully"])
def test_force_close_asks_with_a_warning(monkeypatch, text):
    monkeypatch.setattr(desktop_agent.system_control, "check_force", lambda name: (name, None))
    forced = []
    monkeypatch.setattr(desktop_agent.system_control, "force_close",
                        lambda name: forced.append(name) or Result(True, "Force closed"))
    questions = []
    agent = Agent(confirm=lambda q: questions.append(q) or True)
    assert agent.handle(text).success
    assert questions == ["Force close calculator? Anything unsaved in it will be lost."]
    assert forced == ["calculator"]


def test_force_it_means_the_last_app(monkeypatch):
    monkeypatch.setattr(desktop_agent.system_control, "check_force", lambda name: (name, None))
    forced = []
    monkeypatch.setattr(desktop_agent.system_control, "force_close",
                        lambda name: forced.append(name) or Result(True, "Force closed"))
    agent = Agent(confirm=lambda q: True)
    agent.runner.last_target = "calculator"
    agent.handle("force it")
    assert forced == ["calculator"]


@pytest.mark.parametrize("text, args", [
    ("search for cats", {"query": "cats"}),
    ("google weather lahore", {"query": "weather lahore"}),
    ("search for lofi on youtube", {"query": "lofi", "site": "youtube"}),
    ("search youtube for lofi", {"query": "lofi", "site": "youtube"}),
])
def test_search_opens_the_browser(monkeypatch, text, args):
    from modules import tool_registry
    searched = []
    monkeypatch.setattr(tool_registry.get("open_web_search"), "func",
                        lambda **kw: searched.append(kw) or Result(True, "Searching"))
    Agent().handle(text)
    assert searched == [args]


def test_vague_search_asks_what_to_search(monkeypatch):
    # Heard 4 times in the relay voice test, and Max said nothing useful
    from modules import tool_registry
    searched = []
    monkeypatch.setattr(tool_registry.get("open_web_search"), "func",
                        lambda **kw: searched.append(kw) or Result(True, "Searching"))
    agent = Agent()
    questions = []
    agent.ask = lambda q: questions.append(q) or "pakistan cricket score"
    agent.handle("can you search anything")
    assert questions == ["What should I search for?"]
    assert searched == [{"query": "pakistan cricket score"}]


def test_gpu_command_stays_local():
    agent = Agent()
    command, _ = agent.match(agent.normalize("words uses of my GPU"))
    assert command.name == "gpu"


def test_play_unknown_song_plays_the_first_video(agent, monkeypatch):
    from modules import tools
    monkeypatch.setattr(tools, "first_youtube_video", lambda query: "https://www.youtube.com/watch?v=abcdefghijk")
    opened = []
    monkeypatch.setattr(desktop_agent.system_control, "open_url", lambda url, config=None: opened.append(url))
    assert agent.handle("play bachke bachke karan aujla").message == "Playing bachke bachke karan aujla from YouTube."
    assert opened == ["https://www.youtube.com/watch?v=abcdefghijk"]


# ---------- fixes from the relay voice test ----------

@pytest.mark.parametrize("text", ["close yourself close yourself", "close yourself you idiot",
                                  "ok wax close yourself", "max please close yourself now"])
def test_close_yourself_with_extra_words_exits(agent, text):
    assert agent.handle(text).stop


def test_close_never_treats_yourself_as_an_app(agent):
    command, _ = agent.match(agent.normalize("close yourself close yourself"))
    assert command.name == "exit"


def test_misheard_claude_goes_to_the_agent():
    # "ok Claude, check the logs" was heard as "ok close check all logs"
    from brain.base import Reply
    from tests.conftest import FakeProvider
    provider = FakeProvider([Reply(text="Checking the logs.")])
    agent = Agent({"AGENT_TRACE": False}, provider=provider)
    assert agent.handle("ok close check all logs").message == "Checking the logs."


def test_unknown_single_word_app_still_says_so():
    from brain.base import Reply
    from tests.conftest import FakeProvider
    agent = Agent({"AGENT_TRACE": False}, provider=FakeProvider([Reply(text="should not be asked")]))
    assert agent.handle("close flibber").message == "I don't know an app called flibber."


def test_close_what_answer_goes_to_the_agent_too():
    from brain.base import Reply
    from tests.conftest import FakeProvider
    provider = FakeProvider([Reply(text="Okay.")])
    agent = Agent({"AGENT_TRACE": False}, provider=provider)
    agent.ask = lambda question: "check blogs"
    assert agent.handle("ok close").message == "Okay."
    assert "check blogs" in provider.fake_session.sent[0]


@pytest.mark.parametrize("text", ["can you download anything", "what is kova", "who are you"])
def test_short_questions_get_an_answer(text):
    from brain.base import Reply
    from tests.conftest import FakeProvider
    agent = Agent({"AGENT_TRACE": False}, provider=FakeProvider([Reply(text="Here is my answer.")]))
    agent.min_ai_words = 3
    assert agent.handle(text).message == "Here is my answer."


def test_short_noise_is_still_ignored():
    from brain.base import Reply
    from tests.conftest import FakeProvider
    agent = Agent({"AGENT_TRACE": False}, provider=FakeProvider([Reply(text="should not be asked")]))
    agent.min_ai_words = 3
    assert agent.handle("god god").message == "Sorry, I didn't catch that."


@pytest.mark.parametrize("text, name", [("open start menu", "start menu"), ("search in start menu", "start menu"),
                                        ("daily news", "news"), ("list app", "list apps"),
                                        ("open terminal", "open")])
def test_new_phrases(agent, text, name):
    command, _ = agent.match(agent.normalize(text))
    assert command.name == name


def test_search_for_a_website_opens_it(agent, monkeypatch):
    opened = []
    monkeypatch.setattr(desktop_agent.system_control, "open_url", lambda url, config=None: opened.append(url))
    agent.handle("search discord com in browser")
    assert opened == ["https://www.discord.com/"]


# ---------- routing gaps found by a phrase sweep ----------

@pytest.mark.parametrize("text, name, arg", [
    ("google chrome", "open", "google chrome"),          # the app, not a search for "chrome"
    ("google weather lahore", "search", "weather lahore"),
    ("open task manager performance", "task manager tab", "performance"),
    ("open performance tab in task manager", "task manager tab", "performance"),
    ("check my system", "system status", None),
    ("check the pc", "system status", None),
    ("open new windows", "new window", None),           # was new_window(name="s")
    ("open a new window of brave", "new window", "brave"),
    ("new window chrome", "new window", "chrome"),
])
def test_phrase_sweep_routes(text, name, arg):
    agent = Agent()
    normal = agent.normalize(text)
    if text.startswith("google ") and desktop_agent.system_control.is_app_alias(normal):
        normal = f"open {normal}"
    command, found = agent.match(normal)
    assert (command.name, found) == (name, arg)


def test_google_chrome_opens_the_app(monkeypatch):
    opened = []
    monkeypatch.setattr(desktop_agent.system_control, "open_target",
                        lambda name, config=None: opened.append(name) or Result(True, f"Opening {name}"))
    Agent().handle("google chrome")
    assert opened == ["google chrome"]


def test_restart_yourself_uses_the_assistant_name():
    agent = Agent({"ASSISTANT_NAME": "Nova", "WAKE_WORDS": ["nova"]})
    assert agent.match("restart nova")[0].name == "restart yourself"
    assert agent.match("restart max")[0].name == "restart"   # "max" is just an app name now


def test_answer_that_repeats_the_command_uses_its_target(monkeypatch):
    # Heard "can you open", then "can you open a emu 8086" as the answer to "Open what?"
    from modules import system_control as sc
    opened = []
    monkeypatch.setattr(sc, "open_target", lambda name, config=None: opened.append(name) or Result(True, f"Opening {name}"))
    agent = Agent({})
    agent.ask = lambda question: "can you open a emu 8086"
    result = agent.handle("can you open")
    assert opened == ["emu 8086"]
    assert result.success
