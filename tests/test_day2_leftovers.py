import os
import time

import pytest

import main
from brain.base import BrainError
from brain.relay import NOT_WATCHING, RelayProvider
from desktop_agent import Agent, normalize
from modules.result import Result


# ---------- text mode: end of input never crashes ----------

def feed(monkeypatch, lines):
    """Make input() return these lines, then raise EOFError like a closed stdin."""
    queue = list(lines)

    def fake_input(prompt=""):
        if not queue:
            raise EOFError
        return queue.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)


def test_end_of_input_during_a_confirmation_is_a_no(monkeypatch):
    agent = Agent({}, provider=None)
    answers = []
    agent.handle = lambda text: Result(True, f"asked: {answers.append(agent.confirm('Close calculator?')) or answers[-1]}")
    feed(monkeypatch, ["close calculator"])  # then EOF while Max is asking
    assert main.run_text(agent) is None
    assert answers == [False]


@pytest.mark.parametrize("line", ["\x04", "\x1a", " \x04 "])
def test_ctrl_d_or_ctrl_z_typed_on_windows_ends_text_mode(monkeypatch, line):
    agent = Agent({}, provider=None)
    handled = []
    agent.handle = lambda text: handled.append(text) or Result(True, "ok")
    feed(monkeypatch, [line, "time"])
    assert main.run_text(agent) is None
    assert handled == []


def test_typed_returns_none_at_end_of_input(monkeypatch):
    feed(monkeypatch, [])
    assert main.typed("You: ") is None


# ---------- lead-in words ----------

@pytest.mark.parametrize("raw, expected", [
    ("alright open chrome", "open chrome"),
    ("all right, close calculator", "close calculator"),
    ("fine, play lo-fi", "play lo fi"),
    ("right, open notepad", "open notepad"),
    ("okay fine alright mute", "mute"),
    ("right click", "right click"),
])
def test_more_lead_in_words_are_dropped(raw, expected):
    assert normalize(raw) == expected


@pytest.mark.parametrize("answer", ["alright", "all right go", "alright do it"])
def test_alright_is_a_yes(answer):
    assert main.is_yes(answer)


def test_alright_with_a_no_is_still_no():
    assert not main.is_yes("alright no, don't")


# ---------- relay: no deaf waits ----------

def beat(folder, age=0):
    heartbeat = folder / "watching"
    heartbeat.touch()
    stamp = time.time() - age
    os.utime(heartbeat, (stamp, stamp))


def test_relay_fails_at_once_when_nobody_is_watching(tmp_path):
    session = RelayProvider({"RELAY_TIMEOUT": 30}, folder=tmp_path).session("", [])
    started = time.time()
    with pytest.raises(BrainError, match="isn't watching"):
        session.send("hello")
    assert time.time() - started < 1
    assert not (tmp_path / "request.json").exists()


def test_old_heartbeat_counts_as_not_watching(tmp_path):
    beat(tmp_path, age=60)
    provider = RelayProvider({}, folder=tmp_path)
    assert not provider.watching()
    with pytest.raises(BrainError, match=NOT_WATCHING[:20]):
        provider.ask({"user": "hi"})


def test_saying_cancel_stops_the_wait(tmp_path):
    beat(tmp_path)
    provider = RelayProvider({"RELAY_TIMEOUT": 30}, folder=tmp_path)
    heard = iter([False, False, True])
    provider.while_waiting = lambda: next(heard)
    with pytest.raises(BrainError, match="stopped waiting"):
        provider.ask({"user": "hi"})
    assert not (tmp_path / "request.json").exists()


def test_watcher_going_away_mid_wait_gives_up(tmp_path):
    beat(tmp_path)
    provider = RelayProvider({"RELAY_TIMEOUT": 30}, folder=tmp_path)

    def watcher_quits():
        beat(tmp_path, age=60)
        return False

    provider.while_waiting = watcher_quits
    with pytest.raises(BrainError, match="stopped watching"):
        provider.ask({"user": "hi"})


@pytest.mark.parametrize("heard, cancel", [
    ("cancel", True), ("max stop", True), ("never mind", True), ("nevermind", True),
    ("forget it", True), ("open chrome", False), ("stopwatch", False),
])
def test_cancel_words(heard, cancel):
    assert bool(main.CANCEL.search(heard)) is cancel


def test_voice_mode_hooks_cancel_into_the_relay(monkeypatch, tmp_path):
    from modules import voice_handler
    monkeypatch.setattr(voice_handler, "speak", lambda text: None)
    monkeypatch.setattr(voice_handler, "listen_text", lambda **kw: "max goodbye")
    provider = RelayProvider({}, folder=tmp_path)
    agent = Agent({"ASSISTANT_NAME": "Max", "WAKE_WORDS": ["max"]}, provider=provider)
    main.run_voice(agent)
    assert provider.while_waiting is not None
    monkeypatch.setattr(voice_handler, "listen_text", lambda **kw: "cancel that")
    assert provider.while_waiting() is True
    monkeypatch.setattr(voice_handler, "listen_text", lambda **kw: None)
    assert provider.while_waiting() is False
