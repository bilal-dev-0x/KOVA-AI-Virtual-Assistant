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

    def __init__(self, running, closes=True):
        self.running = {name.lower() for name in running}
        self.closes = closes
        self.killed = []

    def names(self):
        return set(self.running)

    def taskkill(self, args, capture_output=True):
        self.killed.append(args[-1])
        if self.closes:
            self.running.discard(args[-1].lower())


@pytest.fixture
def fake(monkeypatch):
    def install(running, closes=True):
        system = FakeSystem(running, closes)
        monkeypatch.setattr(system_control, "_running_names", system.names)
        monkeypatch.setattr(system_control.subprocess, "run", system.taskkill)
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
