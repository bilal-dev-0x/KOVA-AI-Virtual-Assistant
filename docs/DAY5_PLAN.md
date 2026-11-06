# Day 5 (Fri 6 Nov, evening): Developer + gaming companion 💻🎮

**Goal:** Max knows your games and your code: it lists and starts games, gets the PC ready for gaming and back,
and opens, tests and checks your projects. It commits and pushes only when you say yes.
**Roadmap phases:** 6 (Developer) and 7 (Gaming).
**Built:** in one evening, straight after the Day 4 live demo. Tests use fake libraries and a throwaway git repo;
then a visible live demo on this PC.

---

## 1. Games (`modules/games.py`) ✅
| You say | Max |
|---|---|
| "which games are installed on my pc" / "my games" | Lists them, from every place it knows (below) |
| "play valorant", "open far cry", "launch the game outlast" | Starts the game ("play softly" is still a song) |
| "gaming mode" / "lets game" | High performance plan if the PC has one, alerts paused, the apps using the most RAM named, and closed only if you say yes |
| "done gaming" / "gaming mode off" | Plan and alerts back as they were, closed apps reopened |

**Where games are found** (read only when asked; the list is kept for the run, 0.04 s on this PC):
- **Steam:** every library in `libraryfolders.vdf` (C: and `D:\SteamLibrary`); started with `steam://rungameid/`.
  Redistributables and runtimes are left out.
- **Riot:** VALORANT and League of Legends when their folders exist; started through `RiotClientServices.exe`.
- **Epic:** the launcher's manifests (Among Us); started with the Epic launcher link.
- **Other games on D:** a top folder with a game engine's files (`Binaries`, `bin`, `UnityPlayer.dll`, `.pak`,
  `steam_api`) and a game `.exe`. Installers, redistributables and helpers are left out. An `.exe` named like the
  folder wins (`farcry3.exe` for Far Cry 3, not the Ubisoft installer), then one in `Win64`/`bin`. Names are cleaned:
  "Resident-Evil-Biohazard-HD-REMASTER-AnkerGames_2" → "Resident Evil Biohazard HD REMASTER".
- One game found in two places (Epic's Among Us and the AmongUs folder) is listed once.

Found on this PC: Among Us, Far Cry 3, League of Legends, Outlast, Resident Evil Biohazard HD REMASTER,
VALORANT, Warframe.

## 2. Developer companion (`modules/dev.py`) ✅
| You say | Max | Asks? |
|---|---|---|
| "what projects do i have" | The git projects in `D:\Coding Arc` (11) | – |
| "open my kova project" / "open visora in vs code" | VS Code with that folder | – |
| "summarise my visora project" | Code files, languages, branch, last commit, changes | – |
| "run kovas tests" / "test visora" | `python -m pytest` (the project's own venv if it has one), then what failed by name | the brain's own runs ask |
| "git status of kova" / "what changed in kova" | Changed files, commits not pushed, the diff for the brain | – |
| (to the brain) "commit kova with a good message" | `git add -A` + `git commit -m` | yes, showing the message |
| "push kova to github" | `git push` | **every time** (critical) |

- A project goes by its **GitHub name** when it has one: "kova" finds `KOVA-AI-Virtual-Assistant`, whose folder
  still has its old name. "kovas tests" (said for KOVA's) works too.
- Every command is fixed (`git …`, `python -m pytest …`), with no shell; the AI never writes one.
- A commit is refused if a file looks like keys or passwords (`config.json`, `.env`, `*.key`, ...), whatever
  `.gitignore` says.

## 3. The KOVA server knows games and projects too ✅
`list_games`, `launch_game`, `open_project` and `git_status` are in the server's tool list, so these work when
Gemini is out for the day.

---

## Live demo (6 Nov, 17:03–17:06)
- ✅ 7 games listed, gaming mode on and off (this PC has only Balanced; no apps closed because the demo said no)
- ✅ 11 projects, Visora summary, KOVA's git status and changes
- ✅ **KOVA ran its own tests: 1043 passed in 33 s**
- ✅ VS Code opened the KOVA project
- ✅ The KOVA server (Gemini was out) answered "which of my games would run best" with two tools: the game
  list and the system status (RAM at 90 %)

## Not done (Stretch, moved on)
- Per-game profiles (Discord overlay, volume)
- Proposed fixes as a diff with a backup first
- Carried: vision + UI Automation, routine suggestions, learned phrases, spoken progress

## Your test
1. "which games do I have" → "play warframe" (or another light one) → "gaming mode" → say yes or no → "done gaming"
2. "what projects do I have" → "open my visora project" → "run kovas tests"
3. Change a file in a test project → "what changed in it" → ask the brain to commit it with a good message → yes
4. "push it to github" → Max asks first; say no unless you mean it

## Checklist
- [x] Games: find, start, gaming mode, done gaming (22 tests)
- [x] Developer: projects, VS Code, summary, tests, git status/changes, commit, push (23 tests)
- [x] Server tool list includes games and projects
- [x] Visible live demo on this PC
- [ ] Your test → fixes → commit → push
