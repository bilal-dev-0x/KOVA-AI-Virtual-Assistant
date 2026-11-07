"""Day 6 step 4: the Memory tab's logic — list the facts, delete one through the
forget_memory tool. The database is the temp one conftest points Max at."""
import json
from pathlib import Path

from desktop_agent import Agent
from modules import actions, memory_engine
from ui import memory

CONFIG = {"ASSISTANT_NAME": "Max", "WAKE_WORDS": ["max"]}


class Asked:
    """Stands in for the approval pop-up: records the question, answers yes or no."""

    def __init__(self, yes=True):
        self.yes = yes
        self.questions = []

    def __call__(self, question):
        self.questions.append(question)
        return self.yes


def agent(yes=True):
    asked = Asked(yes)
    return Agent(dict(CONFIG), confirm=asked, provider=None), asked


def remember(text):
    fact, _ = memory_engine.get_memory().remember(text)
    return fact


def test_no_memories_means_an_empty_list():
    assert memory.rows() == []


def test_rows_lists_the_facts_with_their_numbers():
    first = remember("I like lo-fi when coding")
    second = remember("My project folder is D:/Code")
    # The store's own order: newest first
    assert memory.rows() == [(second.id, "My project folder is D:/Code"),
                             (first.id, "I like lo-fi when coding")]
    assert memory.line(first.id, first.text) == f"#{first.id}  I like lo-fi when coding"


def test_deleting_asks_once_through_the_pop_up_and_removes_the_fact():
    fact = remember("I like lo-fi when coding")
    kova, asked = agent(yes=True)

    result = memory.delete(kova, fact.id)

    assert result.success
    assert asked.questions == [f"Should I forget memory number {fact.id}?"]
    assert memory_engine.get_memory().get(fact.id) is None
    assert memory.rows() == []


def test_saying_no_at_the_pop_up_keeps_the_fact():
    fact = remember("I like lo-fi when coding")
    kova, asked = agent(yes=False)

    result = memory.delete(kova, fact.id)

    assert not result.success
    assert len(asked.questions) == 1
    assert memory_engine.get_memory().get(fact.id) is not None


def test_a_number_that_does_not_exist_is_refused_without_asking():
    remember("I like lo-fi when coding")
    kova, asked = agent(yes=True)

    result = memory.delete(kova, 999)

    assert not result.success
    assert "999" in result.message
    assert asked.questions == []          # the check runs before any pop-up
    assert len(memory.rows()) == 1


def test_a_tool_blocked_in_permissions_json_never_reaches_the_pop_up(monkeypatch):
    fact = remember("I like lo-fi when coding")
    kova, asked = agent(yes=True)
    monkeypatch.setattr(kova.runner.permissions, "is_blocked", lambda tool: True)

    result = memory.delete(kova, fact.id)

    assert not result.success and "blocked" in result.message
    assert asked.questions == []
    assert memory_engine.get_memory().get(fact.id) is not None


def test_the_delete_is_logged_like_every_other_tool_call():
    fact = remember("I like lo-fi when coding")
    kova, _ = agent(yes=True)

    memory.delete(kova, fact.id)

    entries = [json.loads(row) for row in Path(actions.ACTIONS_LOG).read_text(encoding="utf-8").splitlines()]
    mine = [entry for entry in entries if entry["tool"] == "forget_memory"]
    assert mine and mine[-1]["source"] == "local"
    assert mine[-1]["approval"] == "confirmed"
    assert mine[-1]["success"] is True
    assert mine[-1]["args"] == {"fact_id": fact.id}
