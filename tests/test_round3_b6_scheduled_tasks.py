import json

from desktop_agent import Agent
from modules import scheduled_tasks
from modules.actions import ActionRunner
from modules.permission_manager import PermissionManager
from modules.result import Result
from modules.tool_registry import get


def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduled_tasks, "TASKS_FILE", tmp_path / "data" / "scheduled_tasks.json")
    scheduled_tasks.stop()


def test_daily_task_persists_normalized_clock_and_open_app_args(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    task, error = scheduled_tasks.add_daily("9 am", "gmail", "open my mail")
    assert error is None
    assert task["at_time"] == "09:00"
    assert task["tool"] == "open_app"
    assert task["args"] == {"name": "gmail"}
    assert json.loads(scheduled_tasks.TASKS_FILE.read_text())["tasks"] == [task]


def test_daily_task_rejects_bad_time(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    task, error = scheduled_tasks.add_daily("sometime", "gmail", "open mail")
    assert task is None
    assert "didn't understand" in error


def test_battery_event_fires_once_until_it_rises_five_points(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    task, error = scheduled_tasks.add_battery_below(20)
    assert error is None
    assert scheduled_tasks.check_battery(19)[0]["id"] == task["id"]
    assert scheduled_tasks.check_battery(18) == []
    assert scheduled_tasks.check_battery(24) == []
    assert scheduled_tasks.check_battery(25) == []
    assert scheduled_tasks.check_battery(19)[0]["id"] == task["id"]


def test_cancel_removes_persisted_task(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    task, _ = scheduled_tasks.add_battery_below(20)
    assert scheduled_tasks.cancel(task["id"])
    assert scheduled_tasks.pending() == []
    assert not scheduled_tasks.cancel(task["id"])


def test_daily_voice_phrase_is_confirmed_once_and_local(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    agent = Agent({}, provider=None)
    asked = []
    agent.confirm = lambda question: asked.append(question) or True
    result = agent.handle("every day at 9 am open my mail")
    assert result.success
    assert "gmail" in result.message.lower()
    assert len(asked) == 1
    assert scheduled_tasks.pending()[0]["args"] == {"name": "gmail"}


def test_battery_voice_phrase_lists_and_cancels_without_pc_action(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    agent = Agent({}, provider=None)
    asked = []
    agent.confirm = lambda question: asked.append(question) or False
    result = agent.handle("when battery is below 20 percent tell me")
    assert result.success
    assert asked == []
    listing = agent.handle("list scheduled tasks")
    assert "below 20 percent" in listing.message
    cancelled = agent.handle("cancel scheduled task 1")
    assert cancelled.success
    assert scheduled_tasks.pending() == []


def test_due_app_action_runs_through_action_runner_and_notifies(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    notifications = []
    action = get("open_app")
    monkeypatch.setattr(action, "func", lambda name: Result(True, f"Opened {name}."))
    runner = ActionRunner(PermissionManager(path=str(tmp_path / "no-permissions.json")),
                          log_path=str(tmp_path / "actions.jsonl"))
    task = {"id": 7, "tool": "open_app", "args": {"name": "gmail"}}
    scheduled_tasks.start(runner, notifications.append)
    scheduled_tasks._run(task)
    scheduled_tasks.stop()
    assert notifications == ["Opened gmail."]
    row = json.loads((tmp_path / "actions.jsonl").read_text().splitlines()[0])
    assert row["source"] == "scheduled task"
    assert row["approval"] == "approved when scheduled"
