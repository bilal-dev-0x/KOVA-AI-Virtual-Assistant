import json
import threading
import time

import pytest

import brain
from brain.base import BrainError, ToolCall
from brain.relay import RelayProvider


class FakeTool:
    name = "open_app"
    description = "Open an app"


def answer_with(folder, make_answer):
    """Play Claude: wait for request.json, then write response.json."""
    seen = []

    def worker():
        request_file = folder / "request.json"
        for _ in range(200):
            if request_file.exists():
                request = json.loads(request_file.read_text(encoding="utf-8"))
                seen.append(request)
                (folder / "response.json").write_text(json.dumps(make_answer(request)), encoding="utf-8")
                return
            time.sleep(0.02)

    thread = threading.Thread(target=worker)
    thread.start()
    return thread, seen


def test_relay_round_trip(tmp_path):
    session = RelayProvider({}, folder=tmp_path).session("be Max", [FakeTool()])
    thread, seen = answer_with(tmp_path, lambda r: {"id": r["id"], "text": "Sure.",
                                                    "calls": [{"name": "open_app", "args": {"name": "chrome"}}]})
    reply = session.send("open chrome")
    thread.join()

    assert seen[0]["user"] == "open chrome"
    assert seen[0]["system"] == "be Max"
    assert seen[0]["tools"] == {"open_app": "Open an app"}
    assert reply.text == "Sure."
    assert [(c.name, c.args) for c in reply.calls] == [("open_app", {"name": "chrome"})]
    assert not (tmp_path / "request.json").exists()


def test_results_are_sent_without_repeating_the_system_prompt(tmp_path):
    session = RelayProvider({}, folder=tmp_path).session("be Max", [FakeTool()])
    session.new = False
    thread, seen = answer_with(tmp_path, lambda r: {"id": r["id"], "text": "Opened Chrome."})
    reply = session.send_results([(ToolCall("open_app", {"name": "chrome"}, "call0"), {"ok": True})])
    thread.join()

    assert "system" not in seen[0]
    assert seen[0]["results"] == [{"name": "open_app", "args": {"name": "chrome"}, "result": {"ok": True}}]
    assert reply.text == "Opened Chrome." and reply.calls == []


def test_old_answer_is_ignored_and_timeout_is_a_brain_error(tmp_path):
    (tmp_path / "response.json").write_text('{"id": "old", "text": "stale"}', encoding="utf-8")
    session = RelayProvider({"RELAY_TIMEOUT": 0.5}, folder=tmp_path).session("", [])
    with pytest.raises(BrainError, match="didn't answer"):
        session.send("hello")
    assert not (tmp_path / "request.json").exists()


def test_relay_is_chosen_from_config():
    assert brain.get_provider({"BRAIN_PROVIDER": "relay"}).name == "relay"
