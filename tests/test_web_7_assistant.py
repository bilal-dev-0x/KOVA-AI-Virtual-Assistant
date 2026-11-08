"""Phase B: persistent Assistant conversation and truthful request states."""

import json

from modules.result import Result
from ui import history
from ui import web_app
from tests.test_web_1_skeleton import make_dashboard, js_values


def test_history_persists_order_and_returns_identifiers(tmp_path, monkeypatch):
    path = tmp_path / "history.jsonl"
    monkeypatch.setattr(history, "HISTORY_FILE", path)

    user = history.append("user", "Hello Max")
    answer = history.append("max", "Hello Commander")

    rows = history.all_messages()
    assert [row["text"] for row in rows] == ["Hello Max", "Hello Commander"]
    assert [row["id"] for row in rows] == [user["id"], answer["id"]]
    assert all(row["time"] for row in rows)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_history_skips_secrets_and_invalid_rows(tmp_path, monkeypatch):
    path = tmp_path / "history.jsonl"
    monkeypatch.setattr(history, "HISTORY_FILE", path)
    assert history.append("user", "My API key is sk-example-secret-value") is None
    assert history.append("unknown", "ignore me") is None
    path.write_text('{broken json}\n{"role":"system","text":"ignore","id":"x"}\n',
                    encoding="utf-8")
    history.append("user", "safe question")
    assert [row["text"] for row in history.all_messages()] == ["safe question"]


def test_history_keeps_only_the_newest_500(tmp_path, monkeypatch):
    path = tmp_path / "history.jsonl"
    monkeypatch.setattr(history, "HISTORY_FILE", path)
    existing = [{"id": str(n), "role": "user", "text": str(n)} for n in range(500)]
    path.write_text("".join(json.dumps(row) + "\n" for row in existing), encoding="utf-8")

    history.append("max", "newest")

    rows = history.all_messages()
    assert len(rows) == 500
    assert rows[0]["text"] == "1"
    assert rows[-1]["text"] == "newest"


def test_dashboard_history_api_returns_saved_conversation():
    dashboard, _, _, _ = make_dashboard()
    try:
        dashboard.show_user("remember this turn")
        dashboard.show_result(Result(True, "I have it."), "text")
        assert [row["text"] for row in dashboard.api.history()] == [
            "remember this turn", "I have it."]
    finally:
        dashboard.destroy()


def test_failure_shows_error_state_until_next_request():
    dashboard, window, _, _ = make_dashboard()
    try:
        window.events.fire("loaded")
        dashboard.show_result(Result(False, "I couldn't complete that request."), "text")
        assert dashboard._status_override == "error"
        assert js_values(window)[-1] == web_app.state_event("error")
        dashboard.show_user("try again")
        assert dashboard._status_override is None
    finally:
        dashboard.destroy()


def test_the_error_state_clears_by_itself(monkeypatch):
    # Live test 8 Nov: without this, "Request failed" stayed until the next request,
    # even after Max went back to sleep
    dashboard, window, _, _ = make_dashboard()
    try:
        window.events.fire("loaded")
        dashboard.show_result(Result(False, "That failed."), "text")
        assert dashboard._status_override == "error"
        monkeypatch.setattr(web_app.time, "monotonic", lambda: dashboard._error_until + 1)
        dashboard._mirror_state()
        assert dashboard._status_override is None
    finally:
        dashboard.destroy()


def test_the_page_waits_for_pywebview_before_reading_history():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "ui" / "web" / "app.js").read_text(encoding="utf-8")
    assert 'addEventListener("pywebviewready", loadHistory' in js
    assert "}, 3000);" not in js  # do not mark history empty before WebView is ready
