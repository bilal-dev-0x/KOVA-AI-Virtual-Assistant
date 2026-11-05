import logging

import pytest

from brain.base import Provider, Reply, Session
from modules import actions, permission_manager, reminders, system_control
from modules.logger import get_logger

# Keep test runs out of the real logs/kova.log
_logger = get_logger()
for _handler in list(_logger.handlers):
    _logger.removeHandler(_handler)
_logger.addHandler(logging.NullHandler())


@pytest.fixture(autouse=True)
def no_start_menu(monkeypatch):
    """Tests must not depend on which apps this PC has installed."""
    monkeypatch.setattr(system_control, "start_menu_apps", lambda: {})


@pytest.fixture(autouse=True)
def no_real_windows(monkeypatch):
    """Tests never see or close the windows that are open on this PC."""
    monkeypatch.setattr(system_control, "app_windows", lambda names: [])
    monkeypatch.setattr(system_control, "_post_close", lambda hwnds: None)


@pytest.fixture(autouse=True)
def no_youtube(monkeypatch):
    """Tests never go online: YouTube 'finds' nothing unless a test says otherwise."""
    from modules import tools
    monkeypatch.setattr(tools, "first_youtube_video", lambda query: None)


@pytest.fixture(autouse=True)
def isolated_files(monkeypatch, tmp_path):
    """No test writes to logs/actions.jsonl or reads your permissions.json."""
    monkeypatch.setattr(actions, "ACTIONS_LOG", str(tmp_path / "actions.jsonl"))
    monkeypatch.setattr(permission_manager, "PERMISSIONS_FILE", str(tmp_path / "permissions.json"))
    from modules import tools, voice_handler
    monkeypatch.setattr(tools, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(voice_handler, "_gender", voice_handler._gender)
    from modules import memory_engine
    monkeypatch.setattr(memory_engine, "DB_FILE", tmp_path / "memory.db")
    # No test runs your routines.json or the example: routine tests write their own
    from modules import alerts, routines
    monkeypatch.setattr(routines, "ROUTINES_FILE", tmp_path / "routines.json")
    monkeypatch.setattr(routines, "EXAMPLE_FILE", tmp_path / "routines.json.example")
    routines._cache["key"] = None
    yield
    alerts.stop()
    if memory_engine._memory is not None:
        memory_engine._memory.close()
        memory_engine._memory = None
    reminders.cancel()


class FakeSession(Session):
    """Plays back scripted AI replies and records what it was sent."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []
        self.resets = 0

    def _next(self):
        return self.replies.pop(0) if self.replies else Reply(text="Done.")

    def send(self, text):
        self.sent.append(text)
        return self._next()

    def send_results(self, results):
        self.sent.append(results)
        return self._next()

    def reset(self):
        self.resets += 1


class FakeProvider(Provider):
    name = "fake"

    def __init__(self, replies=(), search_answer="Search answer."):
        self.fake_session = FakeSession(replies)
        self.search_answer = search_answer
        self.searched = []

    def session(self, system_prompt, tools):
        self.system_prompt = system_prompt
        self.tools = tools
        return self.fake_session

    def search(self, query):
        self.searched.append(query)
        return self.search_answer
