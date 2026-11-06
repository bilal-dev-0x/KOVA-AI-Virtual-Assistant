"""Fixes from the live demo of 6 Nov (Max running on the real PC while Bilal watched)."""
import os
import stat
from pathlib import Path

import pytest

from brain import server
from brain.base import Reply, ToolCall
from desktop_agent import Agent, needs_agent
from modules import agent_loop, files
from tests.conftest import FakeProvider

CONFIG = {"ASSISTANT_NAME": "Max", "WAKE_WORDS": ["max"]}


def make(path, text="x"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_desktop_ini_is_never_renamed_or_deleted():
    # The demo renamed the Desktop's desktop.ini and sent it to the Recycle Bin
    desktop = Path(files.known_folder("desktop"))
    ini = make(desktop / "desktop.ini", "[.ShellClassInfo]")
    assert "hidden Windows file" in files.check_place(ini).message
    assert not files.rename(str(ini), "KOVA Live Done").success and ini.exists()


def test_hidden_files_are_left_out_of_find_and_cant_be_changed(monkeypatch):
    desktop = Path(files.known_folder("desktop"))
    secret = make(desktop / "kova secret.txt")
    monkeypatch.setattr(files, "_hidden", lambda entry: entry.name == "kova secret.txt")
    monkeypatch.setattr(files, "_hidden_path", lambda path: Path(path).name == "kova secret.txt")
    assert not files.find("kova secret").success
    assert "hidden Windows file" in files.check_place(secret).message


@pytest.mark.parametrize("said, agent", [
    ("type hello bilal this is kova testing itself live into the text area", False),
    ("type milk and eggs and bread", False),                       # the text may say "and"
    ("create a folder called kova live test on my desktop", False),
    ("rename it to kova live final version for the demo today", False),
    ("open notepad then type shopping list and save it", True),     # several steps still go to the brain
    ("type shopping list in notepad then save it to my desktop", True),
    ("tell me something interesting about the history of computers", True),
])
def test_long_clear_commands_stay_local(said, agent):
    assert needs_agent(said) == agent


def test_press_backspace_is_a_key():
    agent = Agent(dict(CONFIG))
    assert agent.match("press backspace")[0].name == "press keys"


def test_the_server_can_create_rename_and_delete():
    assert {"create_item", "rename_item", "delete_items"} <= set(server.DEFAULT_TOOLS)


@pytest.mark.parametrize("text, succeeded, claim", [
    ("I've created a folder called KOVA Live Test on your desktop.", set(), "create"),
    ("I've created a folder called KOVA Live Test on your desktop.", {"create_item"}, None),
    ("I have closed Chrome for you.", {"open_app"}, "close"),
    ("I already opened Notepad earlier.", set(), None),
    ("Your RAM is at 94 percent.", set(), None),
])
def test_unbacked_claims(text, succeeded, claim):
    assert agent_loop.unbacked_claim(text, succeeded) == claim


def test_an_answer_claiming_work_that_never_ran_isnt_spoken():
    # The KOVA server said "I've created a folder ..." without any tool that creates
    provider = FakeProvider([Reply(calls=[ToolCall("get_datetime", {})]),
                             Reply(text="I've created a folder called KOVA Live Test on your desktop.")])
    result = Agent(dict(CONFIG), provider=provider).handle("make me a kova test folder on the desktop and tell me")
    assert not result.success
    assert result.message.startswith("Sorry, I said I'd create it, but nothing actually did that.")
    assert "What I did do: tell you the time" in result.message


@pytest.mark.parametrize("text, succeeded, claim", [
    # said by the KOVA server in the voice test, with no tool run
    ("I'll set a reminder for 6:30 PM.", set(), "set"),
    ("I'm coming out of VS Code now. Closing the PowerShell window.", set(), "close"),
    ("I see. I've closed the PowerShell terminal as you requested.", set(), "close"),
    ("I've set a reminder for 6:20 PM.", {"set_reminder"}, None),
    ("Open File Explorer yourself to check.", set(), None),          # advice, not a claim
    ("VALORANT has launched. I'm now in the game.", {"launch_game"}, None),
])
def test_more_unbacked_claims(text, succeeded, claim):
    assert agent_loop.unbacked_claim(text, succeeded) == claim
