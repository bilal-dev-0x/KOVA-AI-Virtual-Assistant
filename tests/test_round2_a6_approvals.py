from desktop_agent import Agent
from modules import tools
from modules.tool_registry import CONTROLLED, CRITICAL, get


def test_normal_close_needs_no_approval_and_force_close_does():
    assert get("close_app").level == CONTROLLED
    assert get("force_close").level == CRITICAL


def test_voice_approval_timeout_says_it_left_it(monkeypatch):
    from ui.voice import VoiceLoop
    voice = VoiceLoop.__new__(VoiceLoop)
    voice.agent = type("Agent", (), {"config": {"APPROVAL_SECONDS": 13}})()
    spoken, listened = [], []
    voice.speak = spoken.append
    monkeypatch.setattr(voice, "listen", lambda **kwargs: listened.append(kwargs) or None, raising=False)
    assert voice.prompt("confirm", "Close it?", lambda answer, question: False) is False
    assert "I didn't hear a yes, so I left it." in spoken
    assert listened[0] == {"wait_seconds": 13, "max_seconds": 13}


def test_local_close_is_not_asked(monkeypatch):
    agent = Agent({}, provider=None)
    asked = []
    agent.confirm = lambda question: asked.append(question) or False
    monkeypatch.setattr(tools.system_control, "check_app", lambda name, must_be_running=False: (name, None))
    monkeypatch.setattr(tools.system_control, "close_app", lambda name, window=None: __import__("modules.result", fromlist=["Result"]).Result(True, "Closed."))
    monkeypatch.setattr(tools.system_control, "app_memory_mb", lambda name: 0)
    result = agent.handle("close download manager")
    assert result.success
    assert asked == []
