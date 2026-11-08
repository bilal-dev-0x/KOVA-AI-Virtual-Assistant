"""Phase C dashboard data and safe action routing."""

import json
from types import SimpleNamespace

from modules import actions, files
from modules.result import Result
from ui import data, web_app
from tests.test_web_1_skeleton import make_dashboard


def test_applications_returns_installed_rows_with_running_status(monkeypatch):
    monkeypatch.setattr(data.system_control, "start_menu_apps", lambda: {
        "google chrome": ("Google Chrome", "chrome.app"),
        "notepad": ("Notepad", "notepad.app"),
    })
    monkeypatch.setattr(data, "_running_process_names", lambda: {"chrome.exe"})
    monkeypatch.setattr(data.system_control, "load_apps", lambda: {"apps": {
        "chrome": {"aliases": ["google chrome"], "process": ["chrome.exe"]},
        "notepad": {"aliases": [], "process": ["notepad.exe"]},
    }})

    answer = data.applications()
    assert answer["items"] == [
        {"name": "Google Chrome", "running": True},
        {"name": "Notepad", "running": False},
    ]
    assert data.applications("note")["items"] == [{"name": "Notepad", "running": False}]


def test_files_lists_only_current_folder_and_searches_safely(tmp_path):
    root = files.known_folder("downloads")
    nested = tmp_path / "home" / "Downloads" / "Project"
    nested.mkdir()
    (tmp_path / "home" / "Downloads" / "visible.txt").write_text("safe", encoding="utf-8")
    (tmp_path / "home" / "Downloads" / ".hidden.txt").write_text("hidden", encoding="utf-8")
    (nested / "needle-report.txt").write_text("found", encoding="utf-8")

    listing = data.files_view(root)
    assert {row["name"] for row in listing["items"]} == {"Project", "visible.txt"}
    assert listing["folder"] == root
    found = data.files_view(root, "needle report")
    assert [row["name"] for row in found["items"]] == ["needle-report.txt"]


def test_files_refuses_blocked_folders(tmp_path):
    blocked = tmp_path / "system"
    blocked.mkdir()
    answer = data.files_view(str(blocked))
    assert answer["items"] == []
    assert "system folder" in answer["error"].lower()


def test_system_status_reuses_cached_metrics_and_reports_unavailable_values(monkeypatch):
    monkeypatch.setattr(data.perf, "disk_info", lambda: [
        {"drive": "C:", "free_gb": 10, "total_gb": 100, "percent": 90}])
    monkeypatch.setattr(data, "_process_rows", lambda: [{"name": "chrome.exe", "ram_mb": 900}])
    for name in ("cpu_percent", "ram_info", "battery_info", "gpu_usage", "_gpu_samples"):
        monkeypatch.setattr(data.perf, name, lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError(f"cached system view should not call {name}")))

    snapshot = data.system_status({"cpu": "23%", "ram": "8.0/16.0 GB", "ram_percent": "50%",
                                   "battery": "80% charging", "gpu": "12%"})
    assert snapshot["cpu_percent"] == 23
    assert snapshot["ram"] == {"used_gb": 8, "total_gb": 16, "percent": 50}
    assert snapshot["battery"] == {"percent": 80, "plugged": True}
    assert snapshot["gpu_percent"] == 12
    assert snapshot["disks"][0]["drive"] == "C:"
    assert snapshot["processes"][0]["name"] == "chrome.exe"


def test_activity_is_newest_first_and_does_not_expose_raw_arguments(tmp_path, monkeypatch):
    path = tmp_path / "actions.jsonl"
    monkeypatch.setattr(actions, "ACTIONS_LOG", str(path))
    rows = [
        {"time": "one", "source": "local", "tool": "create_item", "approval": "not needed",
         "success": True, "message": "Created folder.", "args": {"called": "private"}},
        {"time": "two", "source": "agent", "tool": "rename_item", "approval": "approved",
         "success": True, "message": "Renamed it.", "undo_text": "rename it back", "args": {"secret": "hidden"}},
        {"broken": "record"},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows) + "not json\n", encoding="utf-8")
    agent = SimpleNamespace(runner=SimpleNamespace(undo_stack=[("rename it back", lambda: None)]))

    answer = data.activity(agent)
    assert [row["time"] for row in answer["items"]] == ["two", "one"]
    assert answer["items"][0]["undo_available"] is True
    assert "args" not in answer["items"][0]


def test_action_runner_records_whether_result_can_be_undone(tmp_path, monkeypatch):
    path = tmp_path / "actions.jsonl"
    monkeypatch.setattr(actions, "ACTIONS_LOG", str(path))
    runner = actions.ActionRunner()
    tool = SimpleNamespace(name="rename_item", level="safe")
    result = Result(True, "Renamed.", undo=lambda: Result(True, "Restored."), undo_text="rename it back")
    runner.record("local", tool, {"target": "file.txt"}, "not needed", result)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["undo_text"] == "rename it back"


def test_dashboard_actions_queue_through_controller_and_validate_targets(monkeypatch):
    dashboard, _, controller, _ = make_dashboard()
    try:
        sent = []
        controller.submit = lambda text, source="text": sent.append((text, source)) or True
        monkeypatch.setattr(web_app.data_tab.system_control, "start_menu_apps", lambda: {
            "notepad": ("Notepad", "notepad.app")})
        assert dashboard.api.launch_app("Notepad")["queued"] is True
        assert sent[-1] == ("open Notepad", "ui")
        assert dashboard.api.launch_app("not installed")["queued"] is False

        folder = files.known_folder("downloads")
        assert dashboard.api.file_action("open", folder)["queued"] is True
        assert sent[-1] == (f"open folder {folder}", "ui")
        assert dashboard.api.file_action("shell", folder)["queued"] is False
    finally:
        dashboard.destroy()


def test_phase_c_read_apis_return_real_data_shapes(monkeypatch):
    dashboard, _, _, _ = make_dashboard()
    try:
        monkeypatch.setattr(web_app.data_tab, "applications", lambda query="": {
            "items": [{"name": "Notepad", "running": False}], "error": None})
        monkeypatch.setattr(web_app.data_tab, "files_view", lambda folder="", query="": {
            "roots": [{"name": "Downloads", "path": "D:/Downloads"}], "folder": folder,
            "items": [], "error": None, "truncated": False})
        monkeypatch.setattr(web_app.data_tab, "system_status", lambda cached=None: {
            "cpu_percent": 11, "ram": None, "disks": [], "battery": None,
            "gpu_percent": None, "processes": [], "errors": []})
        monkeypatch.setattr(web_app.data_tab, "activity", lambda agent, limit=100: {
            "items": [], "error": None})

        assert dashboard.api.apps()["items"][0]["name"] == "Notepad"
        assert dashboard.api.files()["roots"][0]["name"] == "Downloads"
        assert dashboard.api.system()["cpu_percent"] == 11
        assert dashboard.api.activity()["items"] == []
    finally:
        dashboard.destroy()


def test_phase_c_screens_are_real_pages_not_placeholders():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "ui" / "web" / "index.html").read_text(encoding="utf-8")
    for phrase in ("Search installed apps", "Search inside this folder", "Top memory use",
                   "Recent actions from KOVA's local log"):
        assert phrase in html
    assert "Not built yet." not in html
