from types import SimpleNamespace

import pytest

from desktop_agent import Agent
from modules import system_control, tools, ui_automation, windows
from modules.result import Result


@pytest.fixture
def fake_fast(monkeypatch):
    opened, waited, traces = [], [], []
    monkeypatch.setattr(system_control, "open_url", lambda url, config=None: opened.append(url))
    monkeypatch.setattr(tools.time, "sleep", lambda seconds: waited.append(seconds))
    monkeypatch.setitem(tools._context, "config", {})
    monkeypatch.setitem(tools._context, "trace", traces.append)
    monkeypatch.setattr(system_control, "window_titled", lambda query: ("chrome", "Fast.com — Internet Speed Test"))
    monkeypatch.setattr(windows, "find", lambda title: (7, "chrome", title))
    monkeypatch.setattr(ui_automation, "_find_named", lambda hwnd, name: [SimpleNamespace(name="Your internet speed is 125 Mbps")]
                        if name == "speed" else [])
    monkeypatch.setattr(ui_automation, "read_controls", lambda hwnd: [])
    return opened, waited, traces


def test_internet_speed_opens_fast_waits_and_reads_the_shown_value(fake_fast):
    opened, waited, traces = fake_fast

    result = tools.internet_speed()

    assert opened == ["https://fast.com/"]
    assert waited == [12]
    assert traces == ["Checking your speed on fast.com. The number shows in a few seconds."]
    assert "125 Mbps" in result.message


def test_internet_speed_never_guesses_when_page_result_is_unreadable(fake_fast, monkeypatch):
    opened, _, _ = fake_fast
    monkeypatch.setattr(system_control, "window_titled", lambda query: None)

    result = tools.internet_speed()

    assert opened == ["https://fast.com/"]
    assert "check the number on the fast.com page" in result.message
    assert "Mbps" not in result.message


@pytest.mark.parametrize("phrase", ["what's my internet speed", "speed test", "wifi speed", "net ki speed"])
def test_internet_speed_phrases_use_the_local_tool(phrase, monkeypatch):
    calls = []
    monkeypatch.setattr(tools.internet_speed.tool, "func", lambda: calls.append(True)
                        or Result(True, "Checking fast.com."))
    agent = Agent({"AGENT_TRACE": False}, provider=None)

    result = agent.handle(phrase)

    assert result.success and calls == [True]
    assert agent.session is None
