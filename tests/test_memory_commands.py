import pytest

from brain.base import Reply, ToolCall
from tests.conftest import FakeProvider
from desktop_agent import Agent
from modules import memory_engine, tool_registry
from modules.memory_engine import as_you, value_of
from modules.tool_registry import CONTROLLED, CRITICAL, SAFE

CONFIG = {"ASSISTANT_NAME": "Max", "WAKE_WORDS": ["max"]}


class Asked:
    """Answers every confirmation with the next scripted answer and records the questions."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.questions = []

    def __call__(self, question):
        self.questions.append(question)
        return self.answers.pop(0) if self.answers else False


def agent(*answers, provider=None):
    asked = Asked(*answers)
    return Agent(CONFIG, confirm=asked, provider=provider), asked


def store():
    return memory_engine.get_memory()


# ---------- remember ----------

def test_remember_keeps_the_words_as_said():
    max_, asked = agent()
    result = max_.handle("Max, remember that my project folder is D:\\Coding Arc\\GitHub Repos, please")
    assert result.success
    assert result.message == "Got it. I'll remember that your project folder is D:\\Coding Arc\\GitHub Repos."
    assert [f.text for f in store().all()] == ["My project folder is D:\\Coding Arc\\GitHub Repos"]
    assert asked.questions == []  # you asked for it, so Max doesn't ask back


@pytest.mark.parametrize("said", ["remember I like lo-fi when coding", "don't forget I like lo-fi when coding",
                                  "keep in mind that I like lo-fi when coding"])
def test_ways_to_say_remember(said):
    max_, _ = agent()
    assert max_.handle(said).message == "Got it. I'll remember that you like lo-fi when coding."


def test_long_remember_stays_local_even_with_a_brain():
    provider = FakeProvider()
    max_, _ = agent(provider=provider)
    result = max_.handle("remember I like lo-fi when coding and jazz when I'm gaming")
    assert result.success
    assert provider.fake_session.sent == []
    assert store().count() == 1


def test_secrets_are_refused():
    max_, _ = agent()
    result = max_.handle("remember my ATM pin is 4321")
    assert not result.success
    assert "don't store" in result.message
    assert store().count() == 0


def test_updating_a_topic_says_what_changed_and_undo_puts_it_back():
    max_, _ = agent()
    max_.handle("remember my project folder is D:\\Code")
    result = max_.handle("remember my project folder is E:\\Work")
    assert result.message == "Updated. Your project folder is E:\\Work now, not D:\\Code."
    assert max_.handle("undo").success
    assert [f.text for f in store().all()] == ["My project folder is D:\\Code"]


def test_saying_it_twice():
    max_, _ = agent()
    max_.handle("remember I like lo-fi")
    assert max_.handle("remember I like lo-fi").message == "I already knew that you like lo-fi."
    assert store().count() == 1


def test_undo_a_new_fact_forgets_it():
    max_, _ = agent()
    max_.handle("remember I like lo-fi")
    assert max_.handle("undo").message.endswith("Okay, I forgot that you like lo-fi.")
    assert store().count() == 0


def test_remember_to_is_a_reminder_not_a_fact():
    max_, _ = agent()
    result = max_.handle("remember to call mom")
    assert "remind me in 10 minutes to call mom" in result.message
    assert store().count() == 0


def test_remember_to_goes_to_the_agent_when_there_is_one():
    provider = FakeProvider([Reply(text="Sure, when?")])
    max_, _ = agent(provider=provider)
    assert max_.handle("remember to call mom").message == "Sure, when?"
    assert store().count() == 0


# ---------- review ----------

@pytest.mark.parametrize("said", ["what do you know about me", "what do you remember about me",
                                  "Max, what do you remember?", "what have I told you", "who am I",
                                  "show my memories", "what's in your memory"])
def test_review(said):
    max_, _ = agent()
    max_.handle("remember I like lo-fi when coding")
    max_.handle("remember my project folder is D drive")
    result = max_.handle(said)
    assert result.message == ("I remember 2 things about you: your project folder is D drive; "
                              "you like lo-fi when coding.")
    assert "#1: I like lo-fi when coding" in result.detail


def test_review_reads_five_and_counts_the_rest():
    for i in range(7):
        store().remember(f"My thing {i} is {i}")
    max_, _ = agent()
    message = max_.handle("what do you know about me").message
    assert message.startswith("I remember 7 things about you:")
    assert message.endswith("; and 2 more.")


def test_review_when_empty():
    max_, _ = agent()
    assert max_.handle("what do you know about me").message.startswith("I don't know anything about you yet")


@pytest.mark.parametrize("said", ["what's my project folder?", "where is my project folder",
                                  "do you remember my project folder", "do you know where my project folder is"])
def test_recall(said):
    max_, _ = agent()
    max_.handle("remember my project folder is D:\\Coding Arc")
    assert max_.handle(said).message == "Your project folder is D:\\Coding Arc."
    assert store().all()[0].uses == 1


def test_recall_with_nothing_saved():
    max_, _ = agent()
    assert max_.handle("what's my favourite food").message == "I don't have anything saved about favourite food."


def test_recall_with_nothing_saved_asks_the_brain():
    provider = FakeProvider([Reply(text="Your IP is on the router page.")])
    max_, _ = agent(provider=provider)
    assert max_.handle("what's my ip address").message == "Your IP is on the router page."


def test_system_questions_are_not_memory():
    max_, _ = agent()
    assert max_.match("whats my battery")[0].name == "battery"
    assert max_.match("what is my cpu usage")[0].name == "cpu"


# ---------- forget ----------

def test_forget_by_topic_asks_first():
    max_, asked = agent(True)
    max_.handle("remember my project folder is D drive")
    max_.handle("remember I like lo-fi")
    result = max_.handle("forget my project folder")
    assert asked.questions == ["Should I forget that your project folder is D drive?"]
    assert result.message == "Okay, I forgot that your project folder is D drive."
    assert [f.text for f in store().all()] == ["I like lo-fi"]


def test_forget_declined_keeps_it():
    max_, _ = agent(False)
    max_.handle("remember I like lo-fi")
    assert max_.handle("forget lo-fi").message == "Okay, I'll keep it."
    assert store().count() == 1


@pytest.mark.parametrize("said", ["forget that", "forget it", "forget about it"])
def test_forget_that_means_the_last_fact(said):
    max_, asked = agent(True)
    max_.handle("remember I like lo-fi")
    max_.handle("remember my project folder is D drive")
    max_.handle(said)
    assert asked.questions == ["Should I forget that your project folder is D drive?"]
    assert [f.text for f in store().all()] == ["I like lo-fi"]


def test_forget_that_after_a_recall():
    max_, asked = agent(True)
    max_.handle("remember I like lo-fi")
    max_.handle("remember my project folder is D drive")
    max_.handle("what's my project folder")
    max_.handle("forget that")
    assert asked.questions == ["Should I forget that your project folder is D drive?"]


def test_forget_that_with_nothing_said_this_run():
    store().remember("I like lo-fi")
    store().remember("My project folder is D drive")
    max_, asked = agent(True)  # nobody answers "Which one should I forget?"
    assert max_.handle("forget that").message == "Okay, never mind."
    assert asked.questions == [] and store().count() == 2


def test_forget_something_unknown():
    max_, asked = agent(True)
    assert max_.handle("forget my favourite food").message == "I don't have anything saved about favourite food."
    assert asked.questions == []


def test_undo_forget_brings_it_back():
    max_, _ = agent(True)
    max_.handle("remember I like lo-fi")
    max_.handle("forget lo-fi")
    assert max_.handle("undo").success
    assert [f.text for f in store().all()] == ["I like lo-fi"]


@pytest.mark.parametrize("said", ["forget everything", "forget everything about me", "forget all",
                                  "delete my memories", "clear your memory", "wipe all memories"])
def test_forget_everything_asks_twice(said):
    max_, asked = agent(True, True)
    max_.handle("remember I like lo-fi")
    max_.handle("remember my project folder is D drive")
    result = max_.handle(said)
    assert asked.questions == ["Should I forget everything I know about you? That's 2 things.",
                               "Are you sure? This can't be undone."]
    assert result.message == "Done. I forgot all 2 things I knew about you."
    assert store().count() == 0


def test_forget_everything_stops_at_the_second_no():
    max_, _ = agent(True, False)
    max_.handle("remember I like lo-fi")
    assert max_.handle("forget everything").message == "Okay, I'll keep them."
    assert store().count() == 1


def test_forget_everything_when_empty():
    max_, asked = agent(True, True)
    assert "don't have any memories" in max_.handle("forget everything").message
    assert asked.questions == []


# ---------- tools for the agent ----------

def test_tool_safety_levels():
    levels = {name: tool_registry.get(name).level for name in
              ("remember", "recall", "list_memories", "forget_memory", "forget_all_memories")}
    assert levels == {"remember": CONTROLLED, "recall": SAFE, "list_memories": SAFE,
                      "forget_memory": CONTROLLED, "forget_all_memories": CRITICAL}


def test_agent_remember_asks_first():
    provider = FakeProvider([Reply(calls=[ToolCall("remember", {"fact": "I like lo-fi when coding"})]),
                             Reply(text="Saved.")])
    max_, asked = agent(True, provider=provider)
    max_.handle("I always listen to lo-fi when I code, keep that in mind")
    assert asked.questions == ["Should I save this to my memory: I like lo-fi when coding?"]
    assert store().count() == 1


def test_agent_remember_of_a_secret_is_refused_before_asking():
    provider = FakeProvider([Reply(calls=[ToolCall("remember", {"fact": "my password is hunter2"})]),
                             Reply(text="I can't store that.")])
    max_, asked = agent(True, provider=provider)
    max_.handle("my email password is hunter2, save it for me")
    assert asked.questions == []
    assert store().count() == 0


def test_agent_forget_by_number_checks_it_exists():
    fact, _ = store().remember("I like lo-fi")
    tool = tool_registry.get("forget_memory")
    assert tool.check(fact_id=fact.id) is None
    assert tool.check(fact_id=999).message == "I don't have a memory number 999."


# ---------- saying facts back ----------

@pytest.mark.parametrize("text, said", [
    ("I like lo-fi when coding", "you like lo-fi when coding"),
    ("My project folder is D:\\Code", "your project folder is D:\\Code"),
    ("I am a student and I'm learning Python", "you are a student and you're learning Python"),
    ("I was born in Lahore", "you were born in Lahore"),
    ("The router is in the hall", "the router is in the hall"),
    ("Bilal is my brother", "Bilal is your brother"),
    ("My ID card is in the drawer", "your ID card is in the drawer"),
    ("Call me Commander", "Call you Commander"),
])
def test_as_you(text, said):
    assert as_you(text) == said


@pytest.mark.parametrize("text, value", [
    ("My project folder is D:\\Code", "D:\\Code"),
    ("My favourite colours are blue and green.", "blue and green"),
    ("I like lo-fi", None),
])
def test_value_of(text, value):
    assert value_of(text) == value
