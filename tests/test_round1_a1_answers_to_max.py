import desktop_agent
from brain.base import Reply, ToolCall
from desktop_agent import Agent
from modules import system_control, tool_registry
from modules.result import Result
from tests.conftest import FakeProvider


def _field_question():
    return Reply(calls=[ToolCall("ui_type", {"text": "my internet speed", "window": "chrome"})])


def _agent(monkeypatch, clock=None):
    typed = []

    def type_text(text, field="", window=""):
        typed.append((text, field, window))
        if len(typed) == 1:
            return Result(False, "Chrome has 2 text fields: Search icon, Address and search bar. Say which one.",
                          halt=True, choices=("Search icon", "Address and search bar"))
        return Result(True, f"Typed {text} into {field}.")

    monkeypatch.setattr(tool_registry.get("ui_type"), "func", type_text)
    provider = FakeProvider([_field_question()])
    agent = Agent({"AGENT_TRACE": False}, provider=provider)
    if clock is not None:
        monkeypatch.setattr(desktop_agent.time, "time", lambda: clock[0])
    return agent, provider.fake_session, typed


def test_short_answer_resumes_the_halted_field_tool(monkeypatch):
    agent, session, typed = _agent(monkeypatch)

    asked = agent.handle("please tell me how to search my internet speed in Chrome")
    assert "Say which one" in asked.message, session.sent
    result = agent.handle("search field")

    assert result.success
    assert typed == [("my internet speed", "", "chrome"), ("my internet speed", "Search icon", "chrome")]
    assert len(session.sent) == 1  # the answer didn't become a Google search or a second AI request


def test_answer_can_include_a_query_and_the_field_choice(monkeypatch):
    agent, _, typed = _agent(monkeypatch)
    agent.handle("please tell me how to search my internet speed in Chrome")

    result = agent.handle("search my internet speed in search icon field")

    assert result.success
    assert typed[-1] == ("my internet speed", "Search icon", "chrome")


def test_clear_command_cancels_pending_question(monkeypatch):
    agent, _, _ = _agent(monkeypatch)
    opened = []
    monkeypatch.setattr(system_control, "open_target", lambda name, config=None:
                        opened.append(name) or Result(True, f"Opened {name}"))
    monkeypatch.setattr(system_control, "check_app", lambda name, must_be_running=False: (name, None))

    agent.handle("please tell me how to search my internet speed in Chrome")
    result = agent.handle("open notepad")

    assert result.success and opened == ["notepad"]
    assert agent.pending_question is None


def test_pending_question_expires_after_sixty_seconds(monkeypatch):
    now = [100.0]
    agent, _, _ = _agent(monkeypatch, now)
    agent.handle("please tell me how to search my internet speed in Chrome")
    assert agent.pending_question is not None

    now[0] += 61
    assert agent._continue_question("search field", "search field") is None
    assert agent.pending_question is None
