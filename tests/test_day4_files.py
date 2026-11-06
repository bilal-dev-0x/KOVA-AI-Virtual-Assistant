"""Day 4 step 3: file tools, all inside temp folders. conftest makes Desktop, Documents, Downloads, ...
temp folders and stops anything from being opened or recycled for real."""
import os
import time
from pathlib import Path

import pytest

from desktop_agent import Agent
from modules import files, tool_registry
from tests.conftest import FakeProvider
from brain.base import Reply, ToolCall

CONFIG = {"ASSISTANT_NAME": "Max", "WAKE_WORDS": ["max"]}
OLD = time.time() - 3600


def folder(key):
    return Path(files.known_folder(key))


def make(path, text="x", old=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if old:
        os.utime(path, (OLD, OLD))
    return path


class Answers:
    def __init__(self, *answers):
        self.answers, self.questions = list(answers), []

    def __call__(self, question):
        self.questions.append(question)
        return self.answers.pop(0) if self.answers else False


def max_agent(*answers, provider=None):
    asked = Answers(*answers)
    return Agent(dict(CONFIG), confirm=asked, provider=provider), asked


@pytest.fixture
def opened(monkeypatch):
    seen = []
    monkeypatch.setattr(files, "_start", seen.append)
    return seen


@pytest.fixture
def recycled(monkeypatch):
    """A fake Recycle Bin: the paths disappear and are remembered."""
    import shutil
    seen = []

    def recycle(paths):
        for p in paths:
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
        seen.extend(paths)
        return True
    monkeypatch.setattr(files, "_recycle", recycle)
    return seen


# ---------- places ----------

def test_your_folders_are_allowed_and_others_are_not(tmp_path):
    assert files.check_place(folder("documents") / "cv.pdf") is None
    assert "only work in your own folders" in files.check_place(tmp_path / "elsewhere" / "x.txt").message
    assert "system folder" in files.check_place(tmp_path / "system" / "x.dll").message
    sneaky = folder("documents") / ".." / ".." / "system" / "x.dll"
    assert "system folder" in files.check_place(sneaky).message
    assert "one of your main folders" in files.check_place(folder("desktop")).message
    assert files.check_place(folder("desktop"), change=False) is None


def test_windows_steam_and_installed_games_are_never_changed():
    steam = folder("documents") / "SteamLibrary" / "steamapps"
    steam.mkdir(parents=True)
    assert "Windows or Steam" in files.check_place(steam).message
    assert "Windows or Steam" in files.check_place(make(folder("documents") / "pagefile.sys")).message
    game = folder("documents") / "Far-Cry-3"
    make(game / "bin" / "farcry3.exe")
    assert "installed program" in files.check_place(game).message
    make(folder("documents") / "notes" / "todo.txt")
    assert files.check_place(folder("documents") / "notes") is None


def test_a_file_inside_a_game_is_never_changed():
    game = folder("documents") / "Riot Games" / "VALORANT"
    make(game / "VALORANT.exe")
    pak = make(game / "live" / "x.pak")          # the folder above has the program
    dll = make(game / "engine.dll")              # right next to it
    assert "part of an installed program" in files.check_place(pak).message
    assert "part of an installed program" in files.check_place(dll).message


def test_loose_files_next_to_a_project_folder_with_a_program_are_fine():
    arc = folder("documents") / "Coding Arc"
    make(arc / "Learning C++" / "a.exe")         # a compiled program one level down
    loose = make(arc / "Project 1.py")
    assert files.check_place(loose) is None


def test_a_setup_exe_in_downloads_doesnt_lock_the_other_downloads():
    make(folder("downloads") / "setup.exe")
    assert files.check_place(make(folder("downloads") / "cv.pdf")) is None


def test_place_words():
    assert files.place("my desktop") == files.known_folder("desktop")
    assert files.place("the downloads folder") == files.known_folder("downloads")
    assert files.place("nowhere at all") is None


# ---------- find and open ----------

def test_find_and_open_by_number(opened):
    make(folder("documents") / "cv_2026.pdf")
    make(folder("downloads") / "My CV.docx")
    make(folder("desktop") / "recvd.txt")  # "cv" inside a word is not a match
    agent, _ = max_agent()
    result = agent.handle("find my cv")
    assert result.success and result.message.startswith("I found 2:")
    assert "recvd" not in result.message
    assert agent.handle("open the second one").success
    assert len(opened) == 1 and Path(opened[0]).name in ("cv_2026.pdf", "My CV.docx")


def test_find_a_misheard_name(opened):
    # Voice test: "Visora BI" was heard as "vichora b i in my disk drive"
    make(folder("documents") / "Visora-BI" / "readme.md")
    agent, _ = max_agent()
    result = agent.handle("find vichora b i in my disk drive")
    assert result.success and "Visora-BI" in result.message


def test_exact_names_win_over_near_ones():
    make(folder("documents") / "visora.txt")
    make(folder("documents") / "vision.txt")
    assert files.find("visora").message == "I found visora.txt in Documents."


def test_spelled_out_letters_join():
    from desktop_agent import normalize
    assert normalize("find v i s o r a", ("max",)) == "find visora"
    assert normalize("is it a b plan", ("max",)) == "is it a b plan"  # two letters stay apart


@pytest.mark.parametrize("said, command", [("mute the video", "mute"), ("open pictures on my pc", "open my folder"),
                                           ("open my downloads", "open my folder"), ("show desktop", "show desktop"),
                                           ("open photos", "open")])
def test_voice_test_phrases(said, command):
    assert max_agent()[0].match(said)[0].name == command


def test_find_nothing():
    assert max_agent()[0].handle("find my passport scan").message == \
        "I couldn't find anything called passport scan in your folders."


def test_find_skips_the_blocked_folders(monkeypatch, tmp_path):
    make(folder("documents") / "kova" / "secret_cv.txt")
    monkeypatch.setattr(files, "blocked_roots", lambda: [str(folder("documents") / "kova")])
    assert not files.find("secret cv").success


@pytest.mark.parametrize("name", ["setup.exe", "run.bat", "link.url", "tool.lnk", "script.py", "console.msc"])
def test_programs_and_scripts_are_never_opened(opened, name):
    make(folder("downloads") / name)
    result = files.open_item(name)
    assert not result.success and "program or a script" in result.message and opened == []


# ---------- create and rename ----------

def test_create_rename_and_undo_keep_the_capitals(opened):
    agent, asked = max_agent()
    assert agent.handle("create a folder called KOVA test on my desktop").success
    assert asked.questions == []                       # undo takes it back, so no question
    assert (folder("desktop") / "KOVA test").is_dir()
    assert agent.handle("rename it to KOVA final").success
    assert (folder("desktop") / "KOVA final").is_dir()
    assert agent.handle("undo").success
    assert (folder("desktop") / "KOVA test").is_dir()


def test_create_a_text_file_through_the_agent():
    provider = FakeProvider([Reply(calls=[ToolCall("create_item", {"called": "shopping list", "kind": "file",
                                                                   "text": "milk\neggs"})])])
    agent, asked = max_agent(True, provider=provider)
    agent.handle("make a shopping list file on my desktop with milk and eggs")
    assert (folder("desktop") / "shopping list.txt").read_text(encoding="utf-8") == "milk\neggs"


@pytest.mark.parametrize("name", ["a/b", "con", "dots.", ""])
def test_bad_names(name):
    assert not files.create(name).success


def test_rename_keeps_the_extension():
    make(folder("documents") / "report.pdf")
    assert files.rename("report.pdf", "report 2.0").success
    assert (folder("documents") / "report 2.0.pdf").exists()
    make(folder("documents") / "cv.pdf")
    assert files.rename("cv.pdf", "my cv.docx").success   # a real extension that was said is kept
    assert (folder("documents") / "my cv.docx").exists()


def test_rename_never_overwrites_or_makes_a_program():
    make(folder("documents") / "a.txt")
    make(folder("documents") / "b.txt")
    assert "already exists" in files.rename("a.txt", "b").message
    assert "program or script" in files.rename("a.txt", "a.exe").message


# ---------- organise ----------

def test_organise_downloads_with_preview_and_undo_after_a_restart():
    downloads = folder("downloads")
    for name in ("a.jpg", "b.png", "c.pdf", "d.zip", "e.weird"):
        make(downloads / name)
    make(downloads / "Pictures" / "a.jpg")              # a name clash: never overwritten
    make(downloads / "big.iso.crdownload")              # still downloading
    make(downloads / "new.png", old=False)              # changed a moment ago
    agent, asked = max_agent(True)
    result = agent.handle("organise my downloads")
    assert result.success
    assert asked.questions == ["I'll sort 5 files in Downloads: 2 pictures, 1 document, 1 archive and "
                               "1 other file. Move them into folders?"]
    assert (downloads / "Pictures" / "a (1).jpg").exists() and (downloads / "Pictures" / "a.jpg").exists()
    assert (downloads / "Archives" / "d.zip").exists() and (downloads / "Other" / "e.weird").exists()
    assert (downloads / "big.iso.crdownload").exists() and (downloads / "new.png").exists()

    restarted, _ = max_agent(True)                       # a new Max: undo comes from the log on disk
    assert restarted.handle("undo organising my downloads").success
    assert sorted(p.name for p in downloads.iterdir() if p.is_file()) == \
        ["a.jpg", "b.png", "big.iso.crdownload", "c.pdf", "d.zip", "e.weird", "new.png"]
    assert not (downloads / "Archives").exists()          # made by Max, now empty, removed
    assert (downloads / "Pictures" / "a.jpg").exists()   # was there before, stays


def test_a_file_named_like_a_category_is_no_crash_and_the_log_is_still_written():
    downloads = folder("downloads")
    make(downloads / "Pictures")                          # a FILE called Pictures
    make(downloads / "a.jpg")
    make(downloads / "c.pdf")
    result = files.organise("downloads")
    assert result.success and "1 couldn't be moved" in result.message
    assert (downloads / "Documents" / "c.pdf").exists() and (downloads / "a.jpg").exists()
    assert files.latest_log() is not None
    assert files.undo_organise("downloads").success
    assert (downloads / "c.pdf").exists()


def test_undo_picks_the_folder_you_name():
    make(folder("downloads") / "a.pdf")
    make(folder("desktop") / "b.pdf")
    files.organise("downloads")
    files.organise("desktop")
    assert files.undo_organise("downloads").message == "Put 1 file back in Downloads."
    assert (folder("desktop") / "Documents" / "b.pdf").exists()


def test_a_failed_undo_keeps_the_log_to_try_again(monkeypatch):
    make(folder("downloads") / "a.pdf")
    make(folder("downloads") / "b.pdf")
    files.organise("downloads")
    real_rename = os.rename

    def busy(src, dst):
        if src.endswith("a.pdf"):
            raise PermissionError("in use")
        real_rename(src, dst)
    monkeypatch.setattr(files.os, "rename", busy)
    result = files.undo_organise("downloads")
    assert not result.success and "couldn't be moved back" in result.message
    monkeypatch.setattr(files.os, "rename", real_rename)
    assert files.undo_organise("downloads").message == "Put 1 file back in Downloads. 1 had been moved or deleted since."


def test_hidden_and_system_files_are_left_alone(monkeypatch):
    make(folder("downloads") / "Thumbs.db")
    make(folder("downloads") / "secret.bin")
    monkeypatch.setattr(files, "_hidden", lambda entry: entry.name == "secret.bin")
    make(folder("downloads") / "c.pdf")
    assert [Path(p).name for p, _ in files.organise_plan(files.known_folder("downloads"))] == ["c.pdf"]


@pytest.mark.parametrize("where", ["d drive", "d drive coding arc", "music"])
def test_only_downloads_desktop_and_documents_are_sorted(where):
    assert not files.organise(where).success


@pytest.mark.parametrize("said", ["organise d drive", "organise my coding arc folder", "sort my music"])
def test_other_folders_are_refused_here_not_swapped_by_the_brain(said):
    # Found in the live test: "organise d drive" went to the brain, which sorted Documents instead
    provider = FakeProvider([Reply(calls=[ToolCall("organise_folder", {"folder": "documents"})])])
    agent, asked = max_agent(True, provider=provider)
    result = agent.handle(said)
    assert "I only sort your Downloads, Desktop or Documents" in result.message
    assert provider.fake_session.sent == [] and asked.questions == []


def test_a_code_project_is_never_sorted():
    (folder("documents") / ".git").mkdir()
    assert "code project" in files.organise("documents").message


# ---------- delete ----------

def test_find_then_delete_it_shows_the_full_path(recycled):
    invoice = make(folder("documents") / "Invoice March.pdf")
    agent, asked = max_agent(True)
    agent.handle("find invoice")
    result = agent.handle("delete it")
    assert asked.questions == [f"Should I move this to the Recycle Bin: {invoice}?"]
    assert result.success and recycled == [str(invoice)] and not invoice.exists()
    assert "Recycle Bin" in result.message


def test_saying_no_keeps_it(recycled):
    note = make(folder("desktop") / "note.txt")
    agent, _ = max_agent(False)
    assert agent.handle("delete note.txt").message == "Okay, I kept it."
    assert note.exists() and recycled == []


def test_removable_and_network_drives_are_refused(monkeypatch, recycled):
    make(folder("desktop") / "note.txt")
    monkeypatch.setattr(files, "_drive_type", lambda root: 2)  # DRIVE_REMOVABLE
    result = files.delete("note.txt")
    assert "no Recycle Bin" in result.message and recycled == []


def test_limits(recycled):
    big = folder("documents") / "big"
    for i in range(files.FOLDER_FILE_LIMIT + 1):
        make(big / f"{i}.txt")
    assert "more than 200 files" in files.delete(str(big)).message
    many = ";".join(str(make(folder("desktop") / f"{i}.txt")) for i in range(files.DELETE_LIMIT + 1))
    assert "at most 20" in files.delete(many).message
    assert recycled == []


def test_the_agents_delete_asks_with_the_full_paths(recycled):
    a = make(folder("desktop") / "a.txt")
    b = make(folder("desktop") / "b.txt")
    provider = FakeProvider([Reply(calls=[ToolCall("delete_items", {"targets": "a.txt; b.txt"})])])
    agent, asked = max_agent(True, provider=provider)
    agent.handle("clean up a and b from my desktop")
    assert asked.questions == [f"This needs extra care. Should I really move these 2 to the Recycle Bin: {a}; {b}?"]
    assert recycled == [str(a), str(b)]


def test_double_null_buffer():
    assert files.double_null(["C:\\a.txt", "D:\\b"]) == "C:\\a.txt\0D:\\b\0\0"


def test_file_tool_levels():
    get = tool_registry.get
    assert get("find_files").level == get("open_file").level == "safe"
    assert get("create_item").level == get("rename_item").level == "safe"          # undo takes them back
    assert get("organise_folder").level == "controlled"                            # the preview is worth a look
    assert get("delete_items").level == "critical"


@pytest.mark.parametrize("said, command", [
    ("find my cv", "find files"),
    ("open the first one", "open found"),
    ("open number 2", "open found"),
    ("create a folder called test on my desktop", "create"),
    ("rename it to kova test", "rename"),
    ("delete it", "delete"),
    ("organise my downloads", "organise"),
    ("undo organising my downloads", "undo organise"),
    ("delete my memories", "forget all"),        # still memory
    ("delete the reminders", "cancel reminders"),  # still reminders
    ("open chrome", "open"),
])
def test_file_phrases(said, command):
    agent, _ = max_agent()
    assert agent.match(said)[0].name == command


def test_spoken_dots_and_extensions(recycled):
    # Voice test: "find pic dot png", "delete the file pitbull png"
    pic = make(folder("downloads") / "pic.png")
    assert files.find("pic dot png").message == "I found pic.png in Downloads."
    pitbull = make(folder("desktop") / "pitbull.png")
    agent, asked = max_agent(True)
    assert agent.handle("delete the file pitbull png").success
    assert recycled == [str(pitbull)] and pic.exists()


def test_undo_removes_a_folder_onedrive_marked_read_only():
    # Live: OneDrive marked the new Documents\Code folder read-only, and undo left it behind
    import stat
    make(folder("documents") / "a.json")
    files.organise("documents")
    code = folder("documents") / "Code"
    (code / "a.json").rename(folder("documents") / "moved.json")    # emptied, like after undo
    os.chmod(code, stat.S_IREAD)
    assert files._remove_empty_folder(str(code)) and not code.exists()
    assert not files._remove_empty_folder(str(folder("documents")))  # not empty: kept
