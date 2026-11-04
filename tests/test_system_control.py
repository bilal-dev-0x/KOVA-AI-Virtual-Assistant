import pytest

from modules import system_control


@pytest.mark.parametrize("name, kind, key", [
    ("chrome", "app", "chrome"),
    ("google chrome", "app", "chrome"),
    ("vscode", "app", "vs code"),
    ("visual studio code", "app", "vs code"),
    ("notpad", "app", "notepad"),        # misheard
    ("youtube", "website", "youtube"),
    ("spotify", "website", "spotify"),
    ("whatsapp", "website", "whatsapp"),
])
def test_resolve(name, kind, key):
    target = system_control.resolve(name)
    assert target is not None
    assert target[:2] == (kind, key)


def test_resolve_unknown():
    assert system_control.resolve("flux capacitor") is None


def test_open_unknown_app():
    assert system_control.open_target("flux capacitor").success is False


def test_open_website_uses_browser(monkeypatch):
    opened = []
    monkeypatch.setattr(system_control, "open_url", lambda url, config=None: opened.append(url))
    assert system_control.open_target("youtube").success
    assert opened == ["https://www.youtube.com/"]


class FakeSystem:
    """Pretends to be the running processes plus taskkill."""

    def __init__(self, running, closes=True, windows=(), background=False):
        self.running = {name.lower() for name in running}
        self.closes = closes
        self.killed = []
        self.window_list = list(windows)  # (handle, title) of visible windows
        self.clicked = []                 # handles that were sent WM_CLOSE
        self.background = background      # keeps running after its last window closes (Chrome)
        self.forced = []

    def app_windows(self, names):
        return list(self.window_list)

    def post_close(self, hwnds):
        hwnds = list(hwnds)
        self.clicked += hwnds
        self.window_list = [w for w in self.window_list if w[0] not in hwnds]
        if not self.window_list and not self.background:
            self.running.clear()

    def names(self):
        return set(self.running)

    def taskkill(self, args, capture_output=True):
        self.killed.append(args[-1])
        if "/F" in args:
            self.forced.append(args[-1])
            self.running.discard(args[-1].lower())
        elif self.closes:
            self.running.discard(args[-1].lower())


@pytest.fixture
def fake(monkeypatch):
    def install(running, closes=True, windows=(), background=False):
        system = FakeSystem(running, closes, windows, background)
        monkeypatch.setattr(system_control, "_running_names", system.names)
        monkeypatch.setattr(system_control.subprocess, "run", system.taskkill)
        # Never send WM_CLOSE to the real windows on this PC
        monkeypatch.setattr(system_control, "app_windows", system.app_windows)
        monkeypatch.setattr(system_control, "_post_close", system.post_close)
        monkeypatch.setattr(system_control, "_processes_named",
                            lambda name: [name] if name.lower() in system.running else [])
        return system
    return install


def test_close_running_app(fake):
    system = fake(["notepad.exe"])
    result = system_control.close_app("notepad")
    assert result.success
    assert system.killed == ["notepad.exe"]


def test_close_app_not_running(fake):
    system = fake([])
    assert "not running" in system_control.close_app("notepad").message
    assert system.killed == []


def test_close_app_that_refuses(fake):
    fake(["notepad.exe"], closes=False)
    result = system_control.close_app("notepad", wait_seconds=0.2)
    assert result.success is False
    assert "still open" in result.message
    assert "force close notepad" in result.message


def test_close_clicks_x_on_windows_when_taskkill_is_ignored(fake, monkeypatch):
    # Store apps like Calculator ignore taskkill; their window has to be closed directly
    system = fake(["calculatorapp.exe"], closes=False, windows=[(1, "Calculator")])
    assert system_control.close_app("calculator", wait_seconds=3).message == "Closed calculator"
    assert system.clicked == [1]
    assert system.forced == []


def test_close_keeps_clicking_until_every_window_is_gone(fake, monkeypatch):
    # Two Chrome windows: one closed, the other stayed open (relay voice test)
    system = fake(["chrome.exe"], closes=False, windows=[(1, "YouTube - Google Chrome"), (2, "Gmail - Google Chrome")])
    clicks = []

    def one_at_a_time(hwnds):
        hwnds = list(hwnds)[:1]
        clicks.append(hwnds)
        system.post_close(hwnds)

    monkeypatch.setattr(system_control, "_post_close", one_at_a_time)
    assert system_control.close_app("chrome", wait_seconds=4).message == "Closed chrome"
    assert clicks == [[1], [2]]


def test_app_in_the_background_counts_as_closed(fake):
    system = fake(["chrome.exe"], closes=False, windows=[(1, "YouTube - Google Chrome")], background=True)
    result = system_control.close_app("chrome", wait_seconds=4)
    assert result.success
    assert result.message.startswith("Closed chrome. It's still running in the background")


def test_close_one_window_only(fake):
    system = fake(["chrome.exe"], windows=[(1, "YouTube - Google Chrome"), (2, "Gmail - Google Chrome")])
    assert system_control.close_app("chrome", window="Gmail").message == "Closed the chrome window Gmail"
    assert system.clicked == [2]
    assert system.killed == []
    assert "chrome.exe" in system.running


def test_close_a_window_that_is_not_there(fake):
    fake(["chrome.exe"], windows=[(1, "YouTube - Google Chrome")])
    assert system_control.close_app("chrome", window="Netflix").success is False


def test_short_title():
    assert system_control.short_title("YouTube - Google Chrome") == "YouTube"
    assert system_control.short_title("Untitled - Notepad") == "Untitled"


def test_force_close_app(fake):
    system = fake(["calculatorapp.exe"], closes=False)
    assert system_control.force_close("calculator").message == "Force closed calculator"
    assert "calculatorapp.exe" in system.forced


def test_force_close_raw_process(fake):
    system = fake(["game.exe"])
    assert system_control.force_close("game").success
    assert system.forced == ["game.exe"]


@pytest.mark.parametrize("name, words", [("file explorer", "not allowed"), ("explorer.exe", "not allowed"),
                                         ("svchost", "part of Windows"), ("csrss.exe", "part of Windows"), ("notepad", "not running")])
def test_force_close_refuses(fake, name, words):
    system = fake(["explorer.exe", "svchost.exe"])
    result = system_control.force_close(name)
    assert result.success is False and words in result.message
    assert system.forced == []


def test_never_closes_file_explorer(fake):
    system = fake(["explorer.exe"])
    assert system_control.close_app("file explorer").success is False
    assert system.killed == []


def test_cannot_close_website(fake):
    fake([])
    assert system_control.close_app("youtube").success is False


def test_running_apps(fake):
    fake(["chrome.exe", "code.exe", "random.exe"])
    message = system_control.running_apps().message
    assert "chrome" in message and "vs code" in message


def test_restart(fake, monkeypatch):
    system = fake(["notepad.exe"])
    monkeypatch.setattr(system_control.time, "sleep", lambda s: None)
    launched = []
    monkeypatch.setattr(system_control, "_launch", lambda entry: launched.append(entry))
    assert system_control.restart_app("notepad").message == "Restarted notepad"
    assert system.killed == ["notepad.exe"]
    assert len(launched) == 1


def test_github_app_opens_desktop_not_website():
    assert system_control.resolve("github app")[:2] == ("app", "github desktop")
    assert system_control.resolve("github")[:2] == ("website", "github")


@pytest.fixture
def start_menu(monkeypatch):
    apps = {"obs studio": ("OBS Studio", "com.obsproject.obs"), "telegram desktop": ("Telegram Desktop", "tg.id")}
    monkeypatch.setattr(system_control, "start_menu_apps", lambda: apps)


def test_start_menu_app_found(start_menu):
    kind, key, entry = system_control.resolve("obs studio")
    assert (kind, key) == ("app", "OBS Studio")
    assert entry["launch"] == {"appid": "com.obsproject.obs"}


def test_start_menu_fuzzy(start_menu):
    assert system_control.resolve("telegram desktp")[1] == "Telegram Desktop"


def test_apps_json_wins_over_start_menu(start_menu, monkeypatch):
    monkeypatch.setattr(system_control, "start_menu_apps", lambda: {"notepad": ("Notepad", "x")})
    assert system_control.resolve("notepad")[2]["launch"] == {"exe": "notepad.exe"}


def test_start_menu_app_cannot_be_closed(start_menu):
    key, problem = system_control.check_app("obs studio")
    assert key is None and "don't know how to close" in problem.message


def test_app_needing_admin_opens_through_the_shell(monkeypatch):
    # Task Manager: Popen fails with error 740, the shell shows Windows' admin prompt instead
    def needs_admin(args):
        error = OSError("The requested operation requires elevation")
        error.winerror = 740
        raise error
    started = []
    monkeypatch.setattr(system_control.subprocess, "Popen", needs_admin)
    monkeypatch.setattr(system_control.os, "startfile", lambda path: started.append(path), raising=False)
    assert system_control.open_target("task manager").success
    assert started == ["taskmgr.exe"]


@pytest.mark.parametrize("name, url", [
    ("discord com", "https://www.discord.com/"),
    ("discord in browser", "https://www.discord.com/"),
    ("youtube in the browser", "https://www.youtube.com/"),
    ("github website", "https://github.com/"),
])
def test_website_words_open_the_website_not_the_app(monkeypatch, name, url):
    opened = []
    monkeypatch.setattr(system_control, "open_url", lambda u, config=None: opened.append(u))
    monkeypatch.setattr(system_control, "_launch", lambda entry: pytest.fail("opened the app"))
    assert system_control.open_target(name).success
    assert opened == [url]


def test_plain_name_still_opens_the_app():
    assert system_control.website_for("discord") is None


def test_admin_app_is_explained_not_forced(fake, monkeypatch):
    system = fake(["taskmgr.exe"], closes=False)
    monkeypatch.setattr(system_control, "_needs_admin", lambda names: True)
    result = system_control.close_app("task manager", wait_seconds=0.2)
    assert "administrator" in result.message and "force close" not in result.message
    assert "administrator" in system_control.force_close("task manager").message
    assert system.forced == []


def test_task_manager_counts_as_admin(monkeypatch):
    # In the voice test Task Manager could be opened for ending, yet taskkill /F failed
    import psutil

    class Proc:
        info = {"name": "Taskmgr.exe"}
        pid = 1

    monkeypatch.setattr(psutil, "process_iter", lambda attrs: [Proc()])
    assert system_control._needs_admin(["Taskmgr.exe"])


def test_start_menu_app_closes_by_its_guessed_process(start_menu, monkeypatch):
    import psutil

    class Proc:
        def __init__(self, name):
            self.info = {"name": name}

    monkeypatch.setattr(psutil, "process_iter", lambda attrs: [Proc("svchost.exe"), Proc("Telegram.exe")])
    key, entry = system_control._resolve_app("telegram desktop")
    assert entry["process"] == ["Telegram.exe"]
    assert system_control.check_app("telegram desktop", must_be_running=False) == ("Telegram Desktop", None)


@pytest.mark.parametrize("app, process", [("Epic Games Launcher", "EpicGamesLauncher.exe"), ("Telegram Desktop", "Telegram.exe")])
def test_guess_process(monkeypatch, app, process):
    import psutil

    class Proc:
        def __init__(self, name):
            self.info = {"name": name}

    monkeypatch.setattr(psutil, "process_iter", lambda attrs: [Proc("chrome.exe"), Proc(process)])
    assert system_control._guess_process(app) == [process]


def test_guess_process_never_picks_windows_or_unrelated(monkeypatch):
    import psutil

    class Proc:
        def __init__(self, name):
            self.info = {"name": name}

    monkeypatch.setattr(psutil, "process_iter", lambda attrs: [Proc("explorer.exe"), Proc("chrome.exe")])
    assert system_control._guess_process("File Explorer") == []
    assert system_control._guess_process("OBS Studio") == []


def test_terminal_opens_but_is_never_closed():
    assert system_control.resolve("powershell")[:2] == ("app", "terminal")
    assert "not allowed" in system_control.check_app("terminal")[1].message
