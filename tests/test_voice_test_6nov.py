"""Phrases from Bilal's voice test of 6 Nov (17:30-18:21) that went to the KOVA server, which then
refused or made things up. They are local commands now."""
import pytest

from brain.base import Reply
from desktop_agent import Agent, parse_reminder, side_by_side_args
from modules import system_control
from tests.conftest import FakeProvider

CONFIG = {"ASSISTANT_NAME": "Max", "WAKE_WORDS": ["max"]}


def agent(provider=None):
    return Agent(dict(CONFIG), provider=provider)


@pytest.mark.parametrize("said, command", [
    ("aligned the chrome window to the left side", "snap window"),
    ("maximize the vs code window at left and maximize the chrome window at right", "side by side"),
    ("open new tab in chrome window", "new tab"),
    ("run the test", "run tests"),
    ("tell me what are reminders i have", "list reminders"),
    ("what are my reminders", "list reminders"),
    ("set a reminder for 6 30 p m", "reminder"),
])
def test_phrases_are_local_commands(said, command):
    assert agent().match(said)[0].name == command


def test_a_long_sentence_with_one_clear_command_stays_local(monkeypatch):
    # "... tell me which apps are open right now in front of me" went to the server, which said it couldn't
    provider = FakeProvider([Reply(text="I can't see your apps.")])
    monkeypatch.setattr(system_control, "running_apps", lambda: __import__("modules.result", fromlist=["R"]).Result(
        True, "Running apps: chrome."))
    result = agent(provider).handle("mother fucking tell me which apps are open right now in front of me")
    assert result.message == "Running apps: chrome." and provider.fake_session.sent == []


def test_a_question_with_a_keyword_still_goes_to_the_brain():
    provider = FakeProvider([Reply(text="Haan, chal jayenge.")])
    assert agent(provider).handle("yah meri gpu per sahi chal jaenge na").message == "Haan, chal jayenge."


def test_two_sides_with_maximise():
    assert side_by_side_args("maximize the vs code window at left and maximize the chrome window at right") == \
        ("vs code", "chrome")
    assert side_by_side_args("minimise it to the right side and minimise the chrome window to the left side") == \
        ("chrome", "it")


def test_a_reminder_at_a_clock_time():
    message, seconds = parse_reminder("set a reminder for 6 30 p m")
    assert seconds > 0
    assert parse_reminder("reminder for 6 20 pm to call ammi")[0] == "call ammi"


def test_windows_powershell_is_the_terminal():
    assert system_control.resolve("windows powershell")[:2] == ("app", "terminal")
    assert system_control.resolve("command prompt")[:2] == ("app", "terminal")
