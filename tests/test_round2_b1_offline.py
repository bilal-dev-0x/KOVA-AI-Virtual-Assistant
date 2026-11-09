from brain import get_provider
from brain.base import BrainError
from desktop_agent import Agent
from modules import agent_loop
from ui.web_app import JsApi


class OfflineSession:
    def send(self, text):
        raise BrainError("connection refused")

    def reset(self):
        pass


class OfflineBrain:
    name = "fake"

    def session(self, *args):
        return OfflineSession()


def test_connection_failure_keeps_local_commands_and_has_one_sentence():
    agent = Agent({}, provider=OfflineBrain())
    assert agent.handle("what time is it").success
    agent.session = OfflineSession()
    result = agent._ask_agent("explain something complicated for me")
    assert result.message == "I couldn't reach the AI right now."


def test_no_keys_selects_no_provider_and_first_run_welcome_shows():
    assert get_provider({}) is None
    api = JsApi(type("Dashboard", (), {"agent": type("Agent", (), {"config": {}})()})())
    assert api.first_run()["show"] is True
    assert Agent({}, provider=None).handle("what time is it").success
