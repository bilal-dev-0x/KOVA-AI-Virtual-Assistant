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


def test_fallback_uses_gemini_answer(agent, monkeypatch):
    monkeypatch.setattr(desktop_agent.ai_brain, "ask", lambda text, config: "Black holes are dense.")
    assert agent.handle("explain black holes").message == "Black holes are dense."


def test_controlled_command_asks_and_can_be_cancelled(monkeypatch):
    monkeypatch.setattr(desktop_agent.system_control, "check_app",
                        lambda name, must_be_running=False: (name, None))
    closed = []
    monkeypatch.setattr(desktop_agent.system_control, "close_app",
                        lambda name: closed.append(name) or Result(True, "Closed"))

    questions = []
    no_agent = Agent(confirm=lambda q: questions.append(q) or False)
    result = no_agent.handle("close notepad")
    assert questions == ["Close notepad?"]
    assert closed == []
    assert result.message == "Okay, cancelled."

    yes_agent = Agent(confirm=lambda q: True)
    assert yes_agent.handle("close notepad").success
    assert closed == ["notepad"]


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
    monkeypatch.setattr(desktop_agent.ai_brain, "ask", lambda t, c: pytest.fail("went to Gemini"))
    asked = []
    agent = Agent()
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
