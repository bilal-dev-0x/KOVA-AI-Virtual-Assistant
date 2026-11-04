import threading
from datetime import datetime

import pytest

from desktop_agent import Agent, parse_reminder
from modules import reminders

NOON = datetime(2026, 11, 4, 12, 0)


@pytest.mark.parametrize("text, message, seconds", [
    ("remind me in 20 minutes to stretch", "stretch", 1200),
    ("remind me to drink water in 5 mins", "drink water", 300),
    ("remind me after 1 hour to call mom", "call mom", 3600),
    ("remind me in an hour to check the oven", "check the oven", 3600),
    ("remind me in half an hour to stand up", "stand up", 1800),
    ("remind me in ten seconds to test", "test", 10),
    ("remind me in 1 5 minutes to eat", "eat", 90),  # "1.5" after punctuation is removed
    ("set a timer for 5 minutes", "timer", 300),
    ("timer for 30 seconds", "timer", 30),
    ("start a 10 minute timer", "timer", 600),
    # Heard in the Day 2 voice test
    ("set a timer of for 30 minutes 30 seconds", "timer", 1830),
    ("remind me in 1 hour and 15 minutes to leave", "leave", 4500),
])
def test_parse_delays(text, message, seconds):
    assert parse_reminder(text) == (message, seconds)


@pytest.mark.parametrize("text, message, hours", [
    ("remind me at 5 pm to go to the gym", "go to the gym", 5),
    ("remind me at 5 p m to go", "go", 5),
    ("remind me to pray at 1 30 pm", "pray", 1.5),
    ("remind me at 3 to call", "call", 3),        # no am/pm: the next 3 o'clock is 3 pm
    ("remind me at 11 am to leave", "leave", 23),  # 11 am already passed today
    ("remind me at 18 00 to cook", "cook", 6),
])
def test_parse_clock_times(text, message, hours):
    assert parse_reminder(text, now=NOON) == (message, hours * 3600)


@pytest.mark.parametrize("text", ["remind me", "remind me to stretch", "remind me at 25 to stretch"])
def test_unparsable_reminders(text):
    assert parse_reminder(text, now=NOON) is None


def test_reminder_fires(monkeypatch):
    fired = threading.Event()
    got = []
    monkeypatch.setattr(reminders, "on_fire", lambda message: got.append(message) or fired.set())
    reminders.add("tea", 0.05)
    assert fired.wait(2)
    assert got == ["tea"]
    assert reminders.pending() == []


def test_cancel_reminders():
    reminders.add("a", 60)
    reminders.add("b", 60)
    assert len(reminders.pending()) == 2
    assert reminders.cancel() == 2
    assert reminders.pending() == []


def test_seconds_until():
    assert reminders.seconds_until("17:30", now=NOON) == 5.5 * 3600
    assert reminders.seconds_until("9:00", now=NOON) == 21 * 3600
    assert reminders.seconds_until("nonsense", now=NOON) is None


def test_reminder_commands_end_to_end():
    agent = Agent({"AGENT_TRACE": False})
    result = agent.handle("remind me in 20 minutes to stretch")
    assert result.message.startswith("Okay, I'll remind you to stretch in 20 minutes")
    assert "stretch" in agent.handle("what reminders do i have").message
    assert agent.handle("undo").message == "Undone. Cancelled the reminder to stretch."
    assert agent.handle("list reminders").message == "You have no reminders."

    assert agent.handle("set a timer for 5 minutes").message.startswith("Timer set for 5 minutes, until")
    assert "your 5 minutes timer is done" in agent.handle("list reminders").message
    assert agent.handle("cancel all timers").message == "Cancelled 1 reminder."


def test_ai_timer_called_timer_is_worded_as_a_timer():
    from modules import tool_registry
    result = tool_registry.get("set_reminder").func(message="Timer", minutes=30.5)
    assert result.message.startswith("Timer set for 30 minutes")
    assert reminders.pending()[0].message == "your 30 minutes timer is done"


def test_reminder_without_time_and_without_gemini():
    assert "Tell me when" in Agent().handle("remind me to stretch").message


@pytest.mark.parametrize("delay, words", [(1, "1 second"), (300, "5 minutes"), (3600, "1 hour"),
                                          (5400, "1 hour 30 minutes")])
def test_describe_delay(delay, words):
    assert reminders.describe_delay(delay) == words
