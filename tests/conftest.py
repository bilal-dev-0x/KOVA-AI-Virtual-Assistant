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
    from modules import screen, windows
    monkeypatch.setattr(windows, "visible_windows", lambda: [])
    monkeypatch.setattr(windows, "_press_win_d", lambda: None)
    monkeypatch.setattr(windows, "_own_windows", lambda: set())
    monkeypatch.setattr(windows, "_ours", lambda hwnd: False)
    monkeypatch.setattr(windows, "_active_popup", lambda hwnd: None)

    def no_screenshots(*args, **kwargs):
        raise AssertionError("tests never take a real screenshot")
    monkeypatch.setattr(screen, "_grab_window", no_screenshots)
    monkeypatch.setattr(screen, "_grab_screen", no_screenshots)

    # Windows OCR: off unless a test turns it on, so results don't depend on this PC
    from modules import ocr

    def no_ocr(*args, **kwargs):
        raise AssertionError("tests never run the real OCR")
    monkeypatch.setattr(ocr, "available", lambda: False)
    monkeypatch.setattr(ocr, "read_text", no_ocr)

    from modules import ui_automation

    def no_real_ui(*args, **kwargs):
        raise AssertionError("tests never read, click or type in real windows")
    monkeypatch.setattr(ui_automation, "_from_seen", lambda *a: None)
    monkeypatch.setattr(ui_automation, "_find_named", lambda *a, **k: [])   # nothing found deeper
    for name in ("_read_tree", "_invoke", "_set_empty_value", "_replace_value", "_focus", "_still_focused",
                 "_type_keys", "_press", "_is_hung", "_window_class", "_is_terminal"):
        monkeypatch.setattr(ui_automation, name, no_real_ui)
    yield
    screen.reset()


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
    from ui import history as ui_history
    monkeypatch.setattr(ui_history, "HISTORY_FILE", tmp_path / "ui-history.jsonl")
    # No test runs your routines.json or the example: routine tests write their own
    from modules import alerts, routines
    monkeypatch.setattr(routines, "ROUTINES_FILE", tmp_path / "routines.json")
    monkeypatch.setattr(routines, "EXAMPLE_FILE", tmp_path / "routines.json.example")
    routines._cache["key"] = None
    # Files: your folders are temp folders, no real drive is searched, nothing is opened or recycled
    from modules import files
    home = tmp_path / "home"
    known = {key: str(home / key.capitalize()) for key in files.KNOWN_FOLDERS}
    for key in files.KNOWN_FOLDERS:
        (home / key.capitalize()).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(files, "_known", known)
    monkeypatch.setattr(files, "_fixed_drives", lambda: [])
    # pytest's temp folder is inside AppData, which Max refuses: tests get their own "system" folders
    monkeypatch.setattr(files, "blocked_roots", lambda: [str(tmp_path / "system"), str(tmp_path / "kova")])
    monkeypatch.setattr(files, "_drive_type", lambda root: files.DRIVE_FIXED)
    monkeypatch.setattr(files, "ORGANISE_LOG_DIR", tmp_path / "organise_log")

    def nothing_real(*args, **kwargs):
        raise AssertionError("tests never open or recycle real files")
    monkeypatch.setattr(files, "_start", nothing_real)
    monkeypatch.setattr(files, "_recycle", nothing_real)
    files._found.clear()
    files._last["path"] = None
    # Games: nothing is looked up on the real drives or started
    from modules import games
    games._cache["games"] = None
    games._mode.update(on=False, plan=None, alerts=False, closed=[])
    for name in ("_steam_games", "_riot_games", "_epic_games", "_folder_games"):
        monkeypatch.setattr(games, name, lambda: iter(()))
    monkeypatch.setattr(games, "_start", nothing_real)
    # Projects: never your real repos (not even KOVA's own), nothing opened, no real pytest run
    from modules import dev
    (tmp_path / "projects").mkdir()
    monkeypatch.setattr(dev, "BASE_DIR", str(tmp_path / "not-a-repo"))
    monkeypatch.setattr(dev, "DEFAULT_ROOTS", [str(tmp_path / "projects")])
    dev._config["roots"] = [str(tmp_path / "projects")]
    dev._last["project"] = None
    monkeypatch.setattr(dev, "_popen", nothing_real)
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


@pytest.fixture(autouse=True)
def no_real_companion(monkeypatch):
    """A test that sends the dashboard to the tray must never open a floating
    companion window (the pet inherits Pill.show, so this covers both)."""
    import ui.pill
    monkeypatch.setattr(ui.pill.Pill, "show", lambda self: False)


@pytest.fixture(autouse=True)
def no_real_startup_folder(monkeypatch, tmp_path):
    """No test reads or writes the real Startup folder."""
    from modules import startup
    monkeypatch.setattr(startup, "startup_dir", lambda: str(tmp_path / "no-startup"))


@pytest.fixture(autouse=True)
def no_window_to_front(monkeypatch):
    """Tests never pull a real window (KOVA's own included) to the front or tap Alt."""
    from ui import web_app
    monkeypatch.setattr(web_app.WebDashboard, "_to_front", lambda self: None)
