import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from modules import system_control
from modules.fuzzy import best_match
from modules.logger import get_logger
from modules.result import Result

# Games on this PC and gaming mode.
#   - Steam: every library in libraryfolders.vdf, games from appmanifest_*.acf (launched by steam://)
#   - Riot: VALORANT and League of Legends when their folders exist (launched by the Riot Client)
#   - Epic: the launcher's manifests (launched by com.epicgames.launcher://)
#   - Other games on a non-system drive: a folder at its top with a game's files (an engine's
#     Binaries\Win64, bin, UnityPlayer.dll, .pak files, steam_api) and a game .exe in it
# Reading is quick and only on request; the list is kept for the run.
#
# Gaming mode: the High performance plan if this PC has it, alerts paused (so Max doesn't talk
# over the game), and the heavy apps named so you can say which to close. "done gaming" puts
# the plan and the alerts back and reopens what was closed.

log = get_logger()

STEAM_SKIP = {"228980", "1070560", "1391110", "1493710", "250820"}  # redistributables, runtimes, SteamVR
RIOT_PRODUCTS = {"valorant": ("VALORANT", "VALORANT"), "league_of_legends": ("League of Legends", "League of Legends")}
JUNK_EXE = re.compile(r"install|setup|redist|physx|crash|unins|uplay|ubisoft|dxsetup|prereq|helper|update|"
                      r"launcher|vcredist|dotnet|directx|report|service|cefprocess|ue4prereq|benchmark|editor|"
                      r"server|xpsepsc|wic_|clwireg|config|settings", re.IGNORECASE)
GAME_MARKERS = {"binaries", "bin", "unityplayer.dll", "steam_api.dll", "steam_api64.dll", "engine", "data_win32",
                "data_win64", "gameassembly.dll"}
NOT_GAMES = {"coding arc", "my downloads", "youtube channel content", "steamlibrary", "riot games", "program files",
             "program files (x86)", "windows", "users", "tmp", "temp", "qbittorrent", "recovery", "programdata"}
SCAN_DEPTH = 4
SCAN_LIMIT = 4000  # entries per folder: a big game is never walked to the end
GAME_WORDS = re.compile(r"[-_ ]?(anker ?games|steamrip(\.com)?|repack|gog|fitgirl|dodi|_\d+)$", re.IGNORECASE)


@dataclass
class Game:
    name: str
    source: str   # steam, riot, epic, folder
    launch: str   # steam://..., an Epic URL, or the .exe
    args: tuple = ()

    def label(self):
        return {"steam": "Steam", "riot": "Riot", "epic": "Epic", "folder": "on your drive"}.get(self.source, "")


_cache = {"games": None}


def installed(refresh=False):
    """Every game found, by name. Read once per run (ask again with refresh)."""
    if _cache["games"] is None or refresh:
        found = {}
        for finder in (_steam_games, _riot_games, _epic_games, _folder_games):
            try:
                for game in finder():
                    # "Among Us" from Epic and the AmongUs folder are one game: the launcher's wins
                    found.setdefault(re.sub(r"[^a-z0-9]", "", game.name.lower()), game)
            except Exception:
                log.exception("Looking for %s games failed", finder.__name__)
        _cache["games"] = sorted(found.values(), key=lambda g: g.name.lower())
        log.info("Found %s games", len(_cache["games"]))
    return _cache["games"]


def find_game(name, strict=False):
    """The installed game name means ("valorant", "far cry", "resident evil"), or None.
    strict: no near spellings (for "play X", which is a song unless it really names a game)."""
    games = installed()
    wanted = _plain(name)
    if not wanted:
        return None
    for game in games:
        if _plain(game.name) == wanted:
            return game
    starts = [g for g in games if _plain(g.name).startswith(wanted) or wanted in _plain(g.name).split()]
    if len(starts) == 1:
        return starts[0]
    if strict:
        return None
    match = best_match(wanted, {_plain(g.name): g for g in games}.keys(), cutoff=0.75)
    return next((g for g in games if _plain(g.name) == match), None) if match else None


def _plain(text):
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower().replace("&", " and ")))


def list_report():
    games = installed(refresh=True)
    if not games:
        return Result(True, "I couldn't find any games on this PC.")
    names = [g.name for g in games]
    message = f"You have {len(names)} game{'s' if len(names) != 1 else ''}: " + \
        (names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]) + "."
    detail = "\n".join(f"  {g.name:<40} {g.label()}" for g in games)
    return Result(True, message, detail=detail)


def launch(name):
    game = find_game(name)
    if game is None:
        known = ", ".join(g.name for g in installed()[:8]) or "none"
        return Result(False, f"I can't find a game called {name}. Your games: {known}.")
    try:
        _start(game)
    except OSError as e:
        log.warning("Launching %s failed: %s", game.name, e)
        return Result(False, f"Windows couldn't start {game.name}.", detail=str(e))
    log.info("Launched %s (%s)", game.name, game.source)
    return Result(True, f"Starting {game.name}. Have fun!")


# ---------- gaming mode ----------

_mode = {"on": False, "plan": None, "alerts": False, "closed": []}
KEEP_OPEN = {"steam", "steamwebhelper", "riot client", "riotclientservices", "epicgameslauncher", "discord",
             "python", "pythonw", "explorer", "obs64", "obs"}


def heavy_apps(count=4):
    """[(app, MB)] using the most RAM, leaving out Windows, Max, launchers and Discord."""
    from modules import performance_monitor
    found = []
    for name, mb in performance_monitor._top_programs(count + 6):
        base = name.lower().removesuffix(".exe")
        if base in KEEP_OPEN or name.lower() in system_control.PROTECTED_PROCESSES:
            continue
        found.append((base, mb))
    return found[:count]


def gaming_mode(config=None, confirm=None):
    """Turn gaming mode on: High performance if there is one, alerts paused, heavy apps offered."""
    from modules import alerts, system_info
    if _mode["on"]:
        return Result(True, "Gaming mode is already on.")
    said = []
    before, before_name = system_info.active_plan()
    plans = system_info.power_plans()
    fast = next((system_info.PLANS[p] for p in ("ultimate performance", "high performance")
                 if system_info.PLANS[p] in plans), None)
    if fast and fast != before:
        switched = system_info.set_power_plan(
            "ultimate performance" if fast == system_info.PLANS["ultimate performance"] else "high performance")
        if switched.success:
            _mode["plan"] = (before, before_name)
            said.append(switched.message.rstrip("."))
    elif not fast:
        said.append("This PC has no High performance plan, so the power plan stays")
    _mode["alerts"] = alerts.running()
    if _mode["alerts"]:
        alerts.stop()
        said.append("alerts are paused")

    heavy = heavy_apps()
    closed = []
    if heavy and confirm:
        listed = ", ".join(f"{app} ({mb / 1024:.1f} GB)" if mb >= 1024 else f"{app} ({mb:.0f} MB)" for app, mb in heavy)
        if confirm(f"These use the most RAM: {listed}. Close them for gaming? Anything unsaved in them is kept, "
                   "they can still ask you to save."):
            for app, _ in heavy:
                if system_control.resolve(app) is None:
                    continue
                if system_control.close_app(app).success:
                    closed.append(app)
    _mode.update(on=True, closed=closed)
    if closed:
        said.append("closed " + ", ".join(closed))
    log.info("Gaming mode on: %s", "; ".join(said))
    message = "Gaming mode is on" + (": " + "; ".join(said) if said else "") + "."
    return Result(True, message + " Say done gaming to put everything back.")


def done_gaming(config=None):
    from modules import alerts, system_info
    if not _mode["on"]:
        return Result(True, "Gaming mode isn't on.")
    said = []
    if _mode["plan"] and _mode["plan"][0]:
        guid, name = _mode["plan"]
        code, _ = system_info._powercfg("/setactive", guid)
        if code == 0:
            said.append(f"back on the {name} plan")
    if _mode["alerts"]:
        alerts.start(config or {})
        said.append("alerts are back on")
    reopened = [app for app in _mode["closed"] if system_control.open_target(app, config or {}).success]
    if reopened:
        said.append("reopened " + ", ".join(reopened))
    _mode.update(on=False, plan=None, alerts=False, closed=[])
    log.info("Gaming mode off: %s", "; ".join(said))
    return Result(True, "Gaming mode is off" + (": " + "; ".join(said) if said else "") + ".")


# ---------- finding games (Windows paths; replaced in tests) ----------

def _steam_root():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            return winreg.QueryValueEx(key, "SteamPath")[0]
    except (OSError, ImportError):
        return None


def _steam_libraries():
    root = _steam_root()
    if not root:
        return []
    libraries = [root]
    try:
        text = (Path(root) / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
        libraries += [p.replace("\\\\", "\\") for p in re.findall(r'"path"\s+"([^"]+)"', text)]
    except OSError:
        pass
    return list(dict.fromkeys(os.path.normcase(os.path.normpath(p)) for p in libraries))


def _steam_games():
    for library in _steam_libraries():
        for manifest in Path(library, "steamapps").glob("appmanifest_*.acf"):
            try:
                text = manifest.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            appid = re.search(r'"appid"\s+"(\d+)"', text)
            name = re.search(r'"name"\s+"([^"]+)"', text)
            if appid and name and appid[1] not in STEAM_SKIP and "redist" not in name[1].lower():
                yield Game(name[1], "steam", f"steam://rungameid/{appid[1]}")


def _riot_games():
    try:
        data = json.loads(Path(os.environ.get("ProgramData", r"C:\ProgramData"), "Riot Games",
                               "RiotClientInstalls.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    client = data.get("rc_live") or data.get("rc_default")
    if not client or not os.path.exists(client):
        return
    root = Path(client).parent.parent  # ...\Riot Games
    for product, (name, folder) in RIOT_PRODUCTS.items():
        if (root / folder).is_dir():
            yield Game(name, "riot", client, (f"--launch-product={product}", "--launch-patchline=live"))


def _epic_games():
    folder = Path(os.environ.get("ProgramData", r"C:\ProgramData"), "Epic", "EpicGamesLauncher", "Data", "Manifests")
    for item in folder.glob("*.item"):
        try:
            data = json.loads(item.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("DisplayName") and data.get("AppName") and os.path.isdir(data.get("InstallLocation", "")):
            yield Game(data["DisplayName"], "epic",
                       f"com.epicgames.launcher://apps/{data['AppName']}?action=launch&silent=true")


def _game_roots():
    """The top folders of the non-system fixed drives (D:\\ here)."""
    from modules import files
    for letter in files._fixed_drives():
        if files._is_system_drive(letter):
            continue
        try:
            for entry in os.scandir(f"{letter}:\\"):
                if entry.is_dir() and entry.name.lower() not in NOT_GAMES and not entry.name.startswith(("$", ".")):
                    yield entry.path
        except OSError:
            continue


def _folder_games():
    for folder in _game_roots():
        exe = main_exe(folder)
        if exe:
            yield Game(game_name(Path(folder).name), "folder", exe)


def game_name(folder_name):
    """'Resident-Evil-Biohazard-HD-REMASTER-AnkerGames_2' -> 'Resident Evil Biohazard HD REMASTER'."""
    name = folder_name
    while GAME_WORDS.search(name):
        name = GAME_WORDS.sub("", name)
    return " ".join(name.replace("-", " ").replace("_", " ").split())


def main_exe(folder):
    """The game's own .exe in folder, or None when the folder doesn't look like a game.

    A game has an engine's files (Binaries, bin, UnityPlayer.dll, .pak, steam_api) near its top.
    Installers and helpers are left out; an .exe named like the folder ("farcry3" in "Far Cry 3")
    wins, then one in Win64 or bin, then the biggest.
    """
    exes, marked, seen = [], False, 0
    stack = [(folder, 0)]
    while stack and seen < SCAN_LIMIT:
        path, depth = stack.pop()
        try:
            entries = list(os.scandir(path))
        except OSError:
            continue
        for entry in entries:
            seen += 1
            low = entry.name.lower()
            if low in GAME_MARKERS or low.endswith(".pak"):
                marked = True
            if entry.is_dir(follow_symlinks=False):
                if depth < SCAN_DEPTH:
                    stack.append((entry.path, depth + 1))
            elif low.endswith(".exe") and not JUNK_EXE.search(low):
                try:
                    exes.append((entry.path, entry.stat().st_size))
                except OSError:
                    continue
    if not exes or not marked:
        return None
    wanted = re.sub(r"[^a-z0-9]", "", game_name(Path(folder).name).lower())

    def score(item):
        path, size = item
        stem = re.sub(r"[^a-z0-9]", "", Path(path).stem.lower().replace("x64", ""))
        exact = stem == wanted                                        # farcry3.exe for "Far Cry 3"
        named = bool(stem) and (stem in wanted or wanted.startswith(stem[:5]))  # farcry3_d3d11.exe too
        placed = any(part.lower() in ("win64", "bin") for part in Path(path).parts)
        return (exact, named, placed, size)
    return max(exes, key=score)[0]


def _start(game):
    if game.source in ("steam", "epic"):
        os.startfile(game.launch)
    else:
        import subprocess
        subprocess.Popen([game.launch, *game.args], cwd=os.path.dirname(game.launch))
