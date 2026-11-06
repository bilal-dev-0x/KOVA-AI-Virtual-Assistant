"""Day 5: the developer companion. A real, throwaway git repo in a temp folder; pytest and VS Code
are faked, and nothing is pushed anywhere."""
import subprocess
from pathlib import Path

import pytest

from brain.base import Reply, ToolCall
from desktop_agent import Agent
from modules import dev
from tests.conftest import FakeProvider

CONFIG = {"ASSISTANT_NAME": "Max", "WAKE_WORDS": ["max"]}


def git(path, *args):
    subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """projects/old-folder-name: a git repo whose GitHub name is KOVA-Test-Assistant."""
    path = Path(dev._config["roots"][0]) / "old-folder-name"
    (path / "tests").mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test")
    git(path, "remote", "add", "origin", "https://github.com/someone/KOVA-Test-Assistant.git")
    (path / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (path / "tests" / "test_main.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "first commit")
    return path


def max_agent(*answers, provider=None):
    asked = []
    answers = list(answers)
    agent = Agent(dict(CONFIG), provider=provider,
                  confirm=lambda q: asked.append(q) or (answers.pop(0) if answers else False))
    return agent, asked


# ---------- projects ----------

def test_a_project_goes_by_its_github_name(repo):
    names = [p.name for p in dev.projects()]
    assert names == ["KOVA Test Assistant"]
    for said in ("kova test", "my kova test project", "old folder name", "kovas test"):
        assert dev.find_project(said).path == str(repo)
    assert dev.find_project("visora") is None


def test_open_in_vscode(repo, monkeypatch):
    opened = []
    monkeypatch.setattr(dev, "_vscode", lambda: "C:/Code.exe")
    monkeypatch.setattr(dev, "_popen", opened.append)
    agent, asked = max_agent()
    assert agent.handle("open my kova test project").message == "Opening KOVA Test Assistant in VS Code."
    assert opened == [["C:/Code.exe", str(repo)]] and asked == []


def test_summary(repo):
    message = dev.summary("kova test").message
    assert "2 code files (2 py)" in message and "on branch main" in message and "first commit" in message


# ---------- tests ----------

def test_run_tests_and_name_the_failures(repo, monkeypatch):
    out = ("..F.\nFAILED tests/test_main.py::test_saves_the_file - assert 1 == 2\n"
           "1 failed, 3 passed in 0.2s\n")
    ran = []
    monkeypatch.setattr(dev, "_run", lambda args, cwd, timeout, env=None: ran.append((args, cwd)) or (1, out))
    result = dev.run_tests("kova test")
    assert not result.success and result.message == "1 of 4 tests of KOVA Test Assistant failed: test saves the file."
    assert ran[0][0][1:] == ["-m", "pytest", "-q", "-p", "no:cacheprovider", "--no-header"]


def test_all_tests_pass(repo, monkeypatch):
    monkeypatch.setattr(dev, "_run", lambda *a, **k: (0, "....\n4 passed in 0.1s\n"))
    assert dev.run_tests("kova test").message == "All 4 tests of KOVA Test Assistant pass."


def test_a_project_without_tests(repo, monkeypatch):
    import shutil
    shutil.rmtree(repo / "tests")
    assert "no tests I know how to run" in dev.run_tests("kova test").message


# ---------- git ----------

def test_git_status_and_changes(repo):
    (repo / "main.py").write_text("print('changed')\n", encoding="utf-8")
    (repo / "new.py").write_text("x = 1\n", encoding="utf-8")
    status = dev.git_status("kova test").message
    assert status.startswith("KOVA Test Assistant: 2 files changed") and "main.py" in status
    changes = dev.git_changes("kova test")
    assert "1 file changed" in changes.message and "1 new files" in changes.message
    assert "print('changed')" in changes.detail


def test_commit_asks_and_refuses_secrets(repo):
    (repo / "main.py").write_text("print('v2')\n", encoding="utf-8")
    (repo / "config.json").write_text('{"GEMINI_API_KEY": "x"}', encoding="utf-8")
    assert "looks like a file with keys" in dev.commit_check("kova test", "update main").message
    (repo / "config.json").unlink()
    result = dev.commit("kova test", "Update the greeting")
    assert result.success and "Committed KOVA Test Assistant" in result.message
    assert subprocess.run(["git", "log", "-1", "--format=%s"], cwd=repo, capture_output=True, text=True).stdout.strip() \
        == "Update the greeting"


def test_the_agent_commits_only_after_a_yes(repo):
    (repo / "main.py").write_text("print('v3')\n", encoding="utf-8")
    provider = FakeProvider([Reply(calls=[ToolCall("git_commit", {"message": "Say hi louder", "name": "kova test"})])])
    agent, asked = max_agent(False, provider=provider)
    agent.handle("commit my kova test changes and write a good message")
    assert asked == ["Should I commit kova test with the message: Say hi louder?"]
    assert dev.git_status("kova test").message.startswith("KOVA Test Assistant: 1 file changed")  # not committed


def test_push_asks_with_the_real_name(repo, monkeypatch):
    pushed = []
    real_git = dev._git
    monkeypatch.setattr(dev, "_git", lambda path, *args, **k: pushed.append(args) or (0, "")
                        if args[:1] == ("push",) else real_git(path, *args, **k))
    agent, asked = max_agent(True)
    result = agent.handle("push kova test to github")
    assert asked == ["Push KOVA Test Assistant to GitHub? Your code goes online."]
    assert result.success and pushed == [("push",)]


def test_saying_no_to_push(repo, monkeypatch):
    monkeypatch.setattr(dev, "_git", lambda *a, **k: pytest.fail("nothing may be pushed"))
    agent, _ = max_agent(False)
    assert agent.handle("push kova test").message == "Okay, I didn't push."


@pytest.mark.parametrize("said, command", [
    ("what projects do i have", "list projects"), ("open my kova project", "open project"),
    ("open visora in vs code", "open project"), ("open my project folder", "open folder"),
    ("run kovas tests", "run tests"), ("run the tests", "run tests"), ("test visora", "run tests"),
    ("git status of kova", "git status"), ("what changed in kova today", "git changes"),
    ("summarise my visora project", "summarise project"), ("push kova to github", "push project"),
])
def test_developer_phrases(said, command):
    assert max_agent()[0].match(said)[0].name == command


def test_tool_levels():
    from modules import tool_registry
    get = tool_registry.get
    assert get("git_status").level == get("open_project").level == "safe"
    assert get("run_tests").level == get("git_commit").level == "controlled"
    assert get("git_push").level == "critical"
