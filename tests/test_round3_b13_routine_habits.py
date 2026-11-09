import json
from datetime import date, timedelta

import modules.tools  # registers the safe built-in actions before the detector checks them
from modules import routine_habits


def _actions(day, tool="open_app"):
    return [{"time": f"{day}T09:00:0{i}", "source": "local", "tool": tool,
             "args": {"name": name}, "success": True}
            for i, name in enumerate(("notepad", "calculator", "chrome"), 1)]


def test_same_three_safe_steps_on_three_days_offers_once(tmp_path):
    state = tmp_path / "state.json"
    today = date(2026, 11, 9)
    for offset in (2, 1):
        assert routine_habits.observe(_actions(today - timedelta(days=offset)), today, state) is None
    key = routine_habits.observe(_actions(today), today, state)
    assert key
    assert routine_habits.observe(_actions(today), today, state) is None
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["pending"]["key"] == key
    assert len(saved["pending"]["steps"]) == 3


def test_actions_can_be_separate_requests_but_must_be_close_and_same_day(tmp_path):
    state = tmp_path / "state.json"
    today = date(2026, 11, 9)
    for offset in (2, 1, 0):
        day = today - timedelta(days=offset)
        assert routine_habits.observe(_actions(day)[:1], today, state) is None
        assert routine_habits.observe(_actions(day)[1:2], today, state) is None
        result = routine_habits.observe(_actions(day)[2:], today, state)
    assert result


def test_unrelated_actions_break_the_sequence(tmp_path):
    state = tmp_path / "state.json"
    today = date(2026, 11, 9)
    rows = _actions(today)
    rows[1]["time"] = f"{today}T10:00:00"
    for offset in (2, 1, 0):
        day = today - timedelta(days=offset)
        adjusted = _actions(day)
        adjusted[1]["time"] = f"{day}T10:00:00"
        assert routine_habits.observe(adjusted, today, state) is None


def test_only_all_successful_safe_local_or_agent_steps_are_considered(tmp_path):
    state = tmp_path / "state.json"
    day = date(2026, 11, 9)
    assert routine_habits.observe(_actions(day, tool="delete_items"), day, state) is None
    failed = _actions(day)
    failed[-1]["success"] = False
    assert routine_habits.observe(failed, day, state) is None
    mixed = _actions(day)
    mixed[-1]["source"] = "scheduled task"
    assert routine_habits.observe(mixed, day, state) is None
    assert not state.exists()


def test_save_requires_a_pending_suggestion_and_writes_only_approved_candidate(tmp_path):
    state, routines = tmp_path / "state.json", tmp_path / "routines.json"
    today = date(2026, 11, 9)
    for offset in (2, 1, 0):
        routine_habits.observe(_actions(today - timedelta(days=offset)), today, state)
    ok, message = routine_habits.save_suggested("morning setup", state, routines)
    assert ok and "3 safe steps" in message
    saved = json.loads(routines.read_text(encoding="utf-8"))["routines"][0]
    assert saved["name"] == "morning setup"
    assert [step["args"]["name"] for step in saved["steps"]] == ["notepad", "calculator", "chrome"]
    assert routine_habits.pending(state) is None


def test_save_refuses_duplicate_names_and_cannot_store_raw_private_arguments(tmp_path):
    state, routines = tmp_path / "state.json", tmp_path / "routines.json"
    today = date(2026, 11, 9)
    for offset in (2, 1, 0):
        routine_habits.observe(_actions(today - timedelta(days=offset)), today, state)
    routines.write_text('{"routines":[{"name":"Morning Setup","steps":[]}]}', encoding="utf-8")
    ok, message = routine_habits.save_suggested("morning setup", state, routines)
    assert not ok and "already exists" in message
    assert "password" not in state.read_text(encoding="utf-8").lower()


def test_explicit_local_save_phrase_uses_controlled_approval(tmp_path, monkeypatch):
    from desktop_agent import Agent

    state, routines = tmp_path / "state.json", tmp_path / "routines.json"
    monkeypatch.setattr(routine_habits, "STATE_FILE", state)
    monkeypatch.setattr(routine_habits, "ROUTINES_FILE", routines)
    today = date(2026, 11, 9)
    for offset in (2, 1, 0):
        routine_habits.observe(_actions(today - timedelta(days=offset)), today, state)
    agent = Agent({}, provider=None)
    questions = []
    agent.confirm = lambda question: questions.append(question) or False
    declined = agent.handle("save this routine as morning setup")
    assert not declined.success and "didn't save" in declined.message.lower()
    assert not routines.exists()
    agent.confirm = lambda question: questions.append(question) or True
    saved = agent.handle("save this routine as morning setup")
    assert saved.success and "saved" in saved.message.lower()
    assert len(questions) == 2
    assert json.loads(routines.read_text(encoding="utf-8"))["routines"][0]["name"] == "morning setup"
