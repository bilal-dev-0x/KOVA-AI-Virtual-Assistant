"""Day 5: games on this PC and gaming mode. Fake libraries and game folders in temp folders:
nothing real is looked up, started, closed or switched."""
from pathlib import Path

import pytest

from desktop_agent import Agent
from modules import alerts, games, system_control, system_info
from modules.result import Result

CONFIG = {"ASSISTANT_NAME": "Max", "WAKE_WORDS": ["max"]}
REAL_STEAM, REAL_FOLDERS = games._steam_games, games._folder_games  # conftest stubs them per test


def make(path, size=10):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


@pytest.fixture
def pc(monkeypatch, tmp_path):
    """A Steam library with two games and a drive with game folders."""
    steam = tmp_path / "steam"
    for appid, name in (("230410", "Warframe"), ("228980", "Steamworks Common Redistributables")):
        make(steam / "steamapps" / f"appmanifest_{appid}.acf").write_text(
            f'"AppState"\n{{\n\t"appid"\t\t"{appid}"\n\t"name"\t\t"{name}"\n}}\n', encoding="utf-8")
    drive = tmp_path / "D"
    make(drive / "Far-Cry-3-AnkerGames" / "Far Cry 3" / "UbisoftConnectInstaller.exe", 900)
    make(drive / "Far-Cry-3-AnkerGames" / "Far Cry 3" / "bin" / "farcry3.exe", 50)
    make(drive / "Far-Cry-3-AnkerGames" / "Far Cry 3" / "bin" / "farcry3_d3d11.exe", 60)
    make(drive / "Outlast-AnkerGames" / "Outlast" / "Binaries" / "Win64" / "OLGame.exe", 300)
    make(drive / "Outlast-AnkerGames" / "Outlast" / "Binaries" / "Win32" / "OLGame.exe", 200)
    make(drive / "TaskbarX" / "TaskbarX.exe", 500)                  # a program, not a game: no engine files
    make(drive / "Coding Arc" / "Learning C++" / "a.exe")
    monkeypatch.setattr(games, "_steam_libraries", lambda: [str(steam)])
    monkeypatch.setattr(games, "_game_roots", lambda: [str(p) for p in drive.iterdir()])
    monkeypatch.setattr(games, "_steam_games", REAL_STEAM)
    monkeypatch.setattr(games, "_folder_games", REAL_FOLDERS)
    started = []
    monkeypatch.setattr(games, "_start", started.append)
    return started


def test_games_are_found(pc):
    names = [g.name for g in games.installed()]
    assert names == ["Far Cry 3", "Outlast", "Warframe"]       # no redistributables, no TaskbarX
    far_cry = games.find_game("far cry")
    assert Path(far_cry.launch).name == "farcry3.exe"          # named like the folder, not the installer
    assert Path(games.find_game("outlast").launch).parts[-2] == "Win64"
    assert games.find_game("warframe").launch == "steam://rungameid/230410"


@pytest.mark.parametrize("folder, name", [
    ("Resident-Evil-Biohazard-HD-REMASTER-AnkerGames_2", "Resident Evil Biohazard HD REMASTER"),
    ("Far-Cry-3-AnkerGames", "Far Cry 3"), ("Cuphead-SteamRIP.com", "Cuphead"), ("AmongUs", "AmongUs"),
])
def test_game_names(folder, name):
    assert games.game_name(folder) == name


def test_one_game_from_two_places_is_listed_once(monkeypatch):
    monkeypatch.setattr(games, "_epic_games", lambda: iter([games.Game("Among Us", "epic", "com.epic://x")]))
    monkeypatch.setattr(games, "_folder_games", lambda: iter([games.Game("AmongUs", "folder", "D:/AmongUs/a.exe")]))
    assert [(g.name, g.source) for g in games.installed()] == [("Among Us", "epic")]


def test_which_games_do_i_have(pc):
    result = Agent(dict(CONFIG)).handle("which games are installed on my pc")
    assert result.message == "You have 3 games: Far Cry 3, Outlast and Warframe."


@pytest.mark.parametrize("said", ["play far cry", "open far cry 3", "launch the game outlast", "start game warframe"])
def test_starting_a_game(pc, said):
    result = Agent(dict(CONFIG)).handle(said)
    assert result.success and result.message.startswith("Starting") and len(pc) == 1


def test_play_a_song_is_still_a_song(pc, monkeypatch):
    from modules import tools
    played = []
    monkeypatch.setattr(system_control, "open_url", lambda url, config=None: played.append(url))
    Agent(dict(CONFIG)).handle("play softly by karan aujla")
    assert pc == [] and played


def test_an_unknown_game(pc):
    assert "can't find a game called tetris" in games.launch("tetris").message


# ---------- gaming mode ----------

@pytest.fixture
def mode(monkeypatch):
    state = {"plan": "balanced-guid", "alerts": True, "closed": [], "opened": [], "switched": []}
    plans = {"balanced-guid": ("Balanced", True), system_info.PLANS["high performance"]: ("High performance", False)}
    monkeypatch.setattr(system_info, "power_plans", lambda: plans)
    monkeypatch.setattr(system_info, "active_plan", lambda: (state["plan"], "Balanced"))

    def switch(plan):
        state["switched"].append(plan)
        return Result(True, "Switched to the High performance power plan.")
    monkeypatch.setattr(system_info, "set_power_plan", switch)
    monkeypatch.setattr(system_info, "_powercfg", lambda *a: state["switched"].append(a) or (0, ""))
    monkeypatch.setattr(alerts, "running", lambda: state["alerts"])
    monkeypatch.setattr(alerts, "stop", lambda: state.update(alerts=False))
    monkeypatch.setattr(alerts, "start", lambda config: state.update(alerts=True))
    monkeypatch.setattr(games, "heavy_apps", lambda count=4: [("chrome", 4800.0), ("code", 900.0)])
    monkeypatch.setattr(system_control, "resolve", lambda name: ("app", name, {}))
    monkeypatch.setattr(system_control, "close_app", lambda name: state["closed"].append(name) or Result(True, ""))
    monkeypatch.setattr(system_control, "open_target",
                        lambda name, config=None: state["opened"].append(name) or Result(True, ""))
    return state


def test_gaming_mode_and_back(mode):
    asked = []
    agent = Agent(dict(CONFIG), confirm=lambda q: asked.append(q) or True)
    result = agent.handle("gaming mode")
    assert "Gaming mode is on" in result.message and "alerts are paused" in result.message
    assert asked == ["These use the most RAM: chrome (4.7 GB), code (900 MB). Close them for gaming? "
                     "Anything unsaved in them is kept, they can still ask you to save."]
    assert mode["closed"] == ["chrome", "code"] and mode["switched"] == ["high performance"]
    result = agent.handle("done gaming")
    assert "reopened chrome, code" in result.message and "alerts are back on" in result.message
    assert mode["opened"] == ["chrome", "code"] and mode["alerts"]


def test_gaming_mode_keeps_apps_when_you_say_no(mode):
    agent = Agent(dict(CONFIG), confirm=lambda q: False)
    agent.handle("start gaming mode")
    assert mode["closed"] == []
    assert "reopened" not in agent.handle("stop gaming mode").message


def test_no_high_performance_plan(mode, monkeypatch):
    monkeypatch.setattr(system_info, "power_plans", lambda: {"balanced-guid": ("Balanced", True)})
    result = Agent(dict(CONFIG), confirm=lambda q: False).handle("lets game")
    assert "no High performance plan" in result.message and mode["switched"] == []


@pytest.mark.parametrize("said, command", [
    ("which games do i have", "list games"), ("gaming mode", "gaming mode"), ("turn on game mode", "gaming mode"),
    ("done gaming", "done gaming"), ("gaming mode off", "done gaming"), ("launch vs code", "open"),
])
def test_game_phrases(said, command):
    assert Agent(dict(CONFIG)).match(said)[0].name == command
