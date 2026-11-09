import pytest

from brain.base import Reply, ToolCall
from desktop_agent import Agent
from modules import tool_registry
from modules.result import Result
from tests.conftest import FakeProvider


@pytest.mark.parametrize("tool_name,args,request_text", [
    ("organise_folder", {"folder": "downloads"}, "connect the downloads and tell me when done"),
    ("delete_items", {"targets": "notes.txt"}, "help me handle notes.txt and tell me when done"),
    ("force_close", {"name": "chrome"}, "close chrome and tell me when done"),
])
def test_sensitive_tool_is_blocked_without_its_intent_word(monkeypatch, tool_name, args, request_text):
    tool = tool_registry.get(tool_name)
    called = []
    monkeypatch.setattr(tool, "func", lambda **kwargs: called.append(kwargs) or Result(True, "Did it."))
    confirmations = []
    provider = FakeProvider([Reply(calls=[ToolCall(tool_name, args)])])
    agent = Agent({"AGENT_TRACE": False}, provider=provider,
                  confirm=lambda question: confirmations.append(question) or True)

    result = agent.handle(request_text)

    assert not result.success
    assert called == [] and confirmations == []
    assert result.message.startswith("Did you want me to organise Downloads?") if tool_name == "organise_folder" else \
        "ask for it directly" in result.message


@pytest.mark.parametrize("tool_name,args,request_text", [
    ("delete_items", {"targets": "notes.txt"}, "delete notes.txt and tell me when done"),
    ("force_close", {"name": "chrome"}, "force close chrome and tell me when done"),
])
def test_explicit_delete_and_force_close_intent_reaches_tool(monkeypatch, tool_name, args, request_text):
    tool = tool_registry.get(tool_name)
    called = []
    monkeypatch.setattr(tool, "func", lambda **kwargs: called.append(kwargs) or Result(True, "Done."))
    monkeypatch.setattr(tool, "level", "safe")
    monkeypatch.setattr(tool, "check", None)
    provider = FakeProvider([Reply(calls=[ToolCall(tool_name, args)]), Reply(text="Done.")])
    agent = Agent({"AGENT_TRACE": False}, provider=provider)

    result = agent.handle(request_text)

    assert result.success
    assert called == [args]


def test_explicit_organise_words_still_reach_the_tool(monkeypatch):
    tool = tool_registry.get("organise_folder")
    called = []
    monkeypatch.setattr(tool, "func", lambda **kwargs: called.append(kwargs) or Result(True, "Sorted."))
    monkeypatch.setattr(tool, "level", "safe")
    provider = FakeProvider([Reply(calls=[ToolCall("organise_folder", {"folder": "downloads"})]),
                             Reply(text="Done.")])
    agent = Agent({"AGENT_TRACE": False}, provider=provider)

    result = agent.handle("organise my downloads and tell me when done")

    assert result.success
    assert called == [{"folder": "downloads"}]


def test_negated_intent_does_not_authorize_a_tool(monkeypatch):
    tool = tool_registry.get("organise_folder")
    called = []
    monkeypatch.setattr(tool, "func", lambda **kwargs: called.append(kwargs) or Result(True, "Sorted."))
    provider = FakeProvider([Reply(calls=[ToolCall("organise_folder", {"folder": "downloads"})])])
    agent = Agent({"AGENT_TRACE": False}, provider=provider)

    result = agent.handle("do not organise my downloads and tell me when done")

    assert not result.success
    assert called == []
