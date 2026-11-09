from pathlib import Path

import pytest

from modules import project_patch
from modules.result import Result


@pytest.fixture
def project_root(tmp_path, monkeypatch):
    monkeypatch.setattr(project_patch, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(project_patch, "_PREVIEWS", {})
    (tmp_path / "sample.py").write_text("one\ntwo\n", encoding="utf-8")
    return tmp_path


def _diff(path="sample.py"):
    return f"--- a/{path}\n+++ b/{path}\n@@ -1,2 +1,2 @@\n-one\n+ONE\n two\n"


def test_preview_then_apply_and_undo_restore_exact_original(project_root):
    previews = []
    assert project_patch.check_args({"path": "sample.py", "patch": _diff()}, previews.append) is None
    assert "-one" in previews[0] and "+ONE" in previews[0]
    applied = project_patch.apply("sample.py", _diff())
    assert applied.success
    assert (project_root / "sample.py").read_text(encoding="utf-8") == "ONE\ntwo\n"
    undone = applied.undo()
    assert undone.success
    assert (project_root / "sample.py").read_text(encoding="utf-8") == "one\ntwo\n"


def test_patch_must_match_approved_preview_and_current_file(project_root):
    assert project_patch.check_args({"path": "sample.py", "patch": _diff()}) is None
    (project_root / "sample.py").write_text("newer\ntwo\n", encoding="utf-8")
    result = project_patch.apply("sample.py", _diff())
    assert not result.success
    assert "changed after preview" in result.message
    assert (project_root / "sample.py").read_text(encoding="utf-8").startswith("newer")


@pytest.mark.parametrize("path,patch", [
    ("../outside.py", _diff("../outside.py")),
    ("config.json", _diff("config.json")),
    ("sample.py", "--- a/sample.py\n+++ b/other.py\n@@ -1 +1 @@\n-one\n+ONE\n"),
    ("sample.py", "--- a/sample.py\n+++ b/sample.py\n@@ -1 +1 @@\n-wrong\n+ONE\n"),
    ("sample.py", "--- a/sample.py\n+++ b/sample.py\n@@ -1 +1 @@\n-key = 'api_key: abcdefghijk'\n+safe\n"),
])
def test_refuses_unsafe_or_stale_diffs_without_changing_file(project_root, path, patch):
    original = (project_root / "sample.py").read_bytes()
    problem = project_patch.check_args({"path": path, "patch": patch})
    assert isinstance(problem, Result) and not problem.success
    assert (project_root / "sample.py").read_bytes() == original


def test_undo_leaves_later_edits_alone(project_root):
    assert project_patch.check_args({"path": "sample.py", "patch": _diff()}) is None
    result = project_patch.apply("sample.py", _diff())
    (project_root / "sample.py").write_text("human edit\n", encoding="utf-8")
    undone = result.undo()
    assert not undone.success
    assert "newer edits alone" in undone.message
    assert (project_root / "sample.py").read_text(encoding="utf-8") == "human edit\n"


def test_registered_patch_tool_refusal_never_writes_and_approval_applies(tmp_path, monkeypatch):
    import modules.tools
    from brain.base import ToolCall
    from modules.actions import ActionRunner
    from modules.agent_loop import run_calls
    from modules.permission_manager import PermissionManager
    from modules.tool_registry import get

    monkeypatch.setattr(project_patch, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(project_patch, "_PREVIEWS", {})
    (tmp_path / "sample.py").write_text("one\ntwo\n", encoding="utf-8")
    tool = get("apply_project_patch")
    runner = ActionRunner(PermissionManager(path=str(tmp_path / "permissions.json")),
                          log_path=str(tmp_path / "actions.jsonl"))
    clean, error = tool.validate({"path": "sample.py", "patch": _diff(), "project": ""})
    assert error is None
    _, refused = run_calls([ToolCall(tool.name, clean)], runner, lambda question: False, trace=lambda _: None)
    assert not refused[0].success
    assert (tmp_path / "sample.py").read_text(encoding="utf-8") == "one\ntwo\n"
    _, approved = run_calls([ToolCall(tool.name, clean)], runner, lambda question: True, trace=lambda _: None)
    assert approved[0].success
    assert (tmp_path / "sample.py").read_text(encoding="utf-8") == "ONE\ntwo\n"
