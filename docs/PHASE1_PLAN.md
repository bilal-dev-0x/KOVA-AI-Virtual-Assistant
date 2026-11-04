# KOVA 2.0 — Phase 1 Plan: Foundation

> **Superseded note:** `modules/ai_brain.py` mentioned below was replaced by the
> `brain/` package (`brain/base.py`, `brain/gemini.py`, `brain/relay.py`,
> `brain/budget.py`) once the provider/agent split landed. This document is kept
> as the Day 1 record; see `KOVA_7_DAY_PLAN.md` for current status.

**Goal:** a stable, testable desktop assistant core that the later phases can build on.
**Roadmap rule:** one milestone at a time. Explain the logic first, test the module on its own, then commit.
**Schedule:** this is Day 1 of the [7-day plan](KOVA_7_DAY_PLAN.md). Milestones 0–4 happen on Day 1; Milestone 5's measurements and README move to Day 7.

---

## Where we are starting from

| Item | Status |
|---|---|
| `modules/voice_handler.py` (`speak`, `listen_with_sounddevice`) | Done, not committed yet |
| `test_voice.py` (manual mic/speaker check) | Done, not committed yet |
| `desktop_agent.py`, `modules/ai_brain.py`, `modules/system_control.py`, `modules/performance_monitor.py` | Empty placeholders |
| Real keys moved out of `config.json.example` into the gitignored `config.json` | Done |

### Known problems found in the current code
1. **`play <song>` never works.** The library keys are mixed case with the artist included (`"Pasoori - Ali Sethi x Shae Gill"`). The command is lowercased and must match a key *exactly*, so it never matches. Milestone 4's fuzzy matching fixes this.
2. **Gemini is required at startup.** KOVA quits without a key and connects to Gemini before any command runs. Milestone 1 fixes this.
3. **Voice only, no text input.** Fixed in Milestone 1.
4. **Everything is imported at startup** (Gemini, pygame, requests). Fixed in Milestones 1 and 4.
5. The Chrome path is hard-coded and looked up again on every loop. Fixed in Milestone 4.
6. `.gitignore` still lists `jarvis_voice.mp3`, but the file is now `kova_voice.mp3`.
7. One library entry has a bad link: `Tum Hi Ho` has a 12-character video ID, and YouTube IDs are 11.

---

## Folder layout at the end of Phase 1

We keep it simple and stay in `modules/`. The roadmap's `core/`, `interfaces/` and other folders come later, when the code really needs them.

```
main.py                    entry point: load config, pick voice/text mode, run the loop
desktop_agent.py           command router: clean input → match command table → Result
apps.json                  app names, aliases and how to launch each one
config.json(.example)      keys + settings (browser, TTS fallback, ...)
music_library.py           unchanged data
modules/
  voice_handler.py         speak / listen (+ offline TTS fallback)
  ai_brain.py              Gemini fallback, imported only when needed
  system_control.py        open / close / restart / list apps
  performance_monitor.py   CPU / RAM / disk on demand
  logger.py                rotating log file in logs/
tests/
  test_router.py
  test_system_control.py
  test_performance_monitor.py
docs/
  PHASE1_PLAN.md           this file
```

---

## Milestone 0: Save the current work and tidy up
**Why:** start from a clean, committed state.

- [x] Remove the unused imports from `main.py` (`gTTS`, `pygame`, `sounddevice`, `os`).
- [x] `.gitignore`: replace `jarvis_voice.mp3` with `kova_voice.mp3` and add `logs/`.
- [x] `voice_handler.speak()`: save the temporary mp3 in the system temp folder instead of the project folder.
- [x] Run `python test_voice.py` and confirm you hear the voice and the recording works.
- [x] Commit: `refactor: extract voice handler into modules/` (done as one Day 1 commit)

**Done when:** KOVA runs exactly as before and `git status` is clean.

---

## Milestone 1: Command router + text mode (the core)
**Why:** everything else plugs into this, and it makes KOVA testable without a microphone.

- [x] `desktop_agent.py`:
  - `normalize(text)`: lowercase it, strip punctuation, drop filler words ("kova", "please", "can you").
  - `Result`: a small object with `success` and `message`.
  - A command table: a list of entries, each holding its trigger words, its handler function and its safety level (`safe` / `controlled` / `critical`). The first match wins, so specific commands go before general ones.
  - `route(text)`: finds the matching command, asks for a yes/no confirmation if it isn't `safe`, runs the handler and returns a Result.
  - No match → AI fallback.
- [x] Move the existing commands into handlers: hello, open YouTube/Google/Spotify, play, news, WhatsApp, date, time.
- [x] New commands: `help` (built automatically from the table) and `exit` / `goodbye`.
- [x] `modules/ai_brain.py`: move the Gemini code here and import it lazily on the first fallback. Without a key or internet, reply "I didn't understand that" and don't crash.
- [x] `main.py`: becomes thin. `python main.py` starts voice mode; `python main.py --text` starts text mode. Output goes through one `respond()` function: print in both modes, speak only in voice mode. Ctrl+C exits cleanly.
- [x] Install `pytest`. In `tests/test_router.py`, test that each phrase reaches the right handler, gibberish reaches the fallback, and a missing Gemini key doesn't crash.
- [x] Commit: `feat: add command router with text and voice modes` (done as one Day 1 commit)

**Done when:** `python main.py --text` works without a Gemini key, and `pytest` passes.

---

## Milestone 2: App control (`system_control.py` + `apps.json`)
**Why:** this is the roadmap's open / close / restart / list requirement.

- [x] `apps.json`: each entry has a display name, aliases (e.g. `vs code`, `vscode`, `code`), how to launch it (exe path, `shell:AppsFolder` ID or URL) and its process name.
- [x] Install `psutil` and add it to `requirements.txt`.
- [x] `open_app(name)` (safe): launch it the way `apps.json` says. WhatsApp moves here.
- [x] `list_apps()` (safe): show which of the configured apps are running.
- [x] `close_app(name)` (controlled): ask first, then close it gently and only escalate if needed.
- [x] `restart_app(name)` (controlled): close, then open.
- [x] Unknown app → a clear message, and a "did you mean…?" suggestion once Milestone 4 lands.
- [x] Tests: name/alias lookup, plus a dry run of close/restart that never touches real processes.
- [x] Commit: `feat: add app control with apps.json mappings` (done as one Day 1 commit)

**Done when:** "open notepad", "list apps", "close notepad" (after a yes) and "restart notepad" all work by voice and by text.

---

## Milestone 3: System monitoring (`performance_monitor.py`)
**Why:** this is the roadmap's on-demand CPU / RAM / disk check.

- [x] `cpu`: percent used, sampled once over ~1 second. No background polling.
- [x] `ram`: used / total GB and percent.
- [x] `disk`: free / total GB for each drive.
- [x] `system status`: one combined summary, with a short spoken version and a detailed printed version.
- [x] Tests: run each function and check the numbers are in sensible ranges.
- [x] Commit: `feat: add on-demand system monitoring` (done as one Day 1 commit)

**Done when:** "system status" answers in under ~2 seconds and adds no idle CPU load.

---

## Milestone 4: Polish & reliability
**Why:** fix the known bugs and make KOVA forgiving of speech-recognition mistakes.

- [x] Fuzzy matching with `difflib` (built into Python): "play pasoori" finds `Pasoori - Ali Sethi x Shae Gill`, and "open spotifi" finds `spotify`. This fixes the broken `play` command.
- [x] Fix the bad `Tum Hi Ho` link.
- [x] Offline voice fallback: when gTTS fails (no internet), speak with `pyttsx3`, loaded only when needed.
- [x] `config.json`: a `BROWSER` setting (a Chrome path or `default`), looked up once instead of on every loop.
- [x] `modules/logger.py`: a rotating log in `logs/kova.log` with command, handler, result and errors. Never log keys.
- [x] Commit: `feat: fuzzy matching, offline TTS fallback, logging` (done as one Day 1 commit)

**Done when:** KOVA still talks with Wi-Fi off, and the `play` command works with partial song names.

---

## Milestone 5: Measure & document
**Why:** the roadmap's Definition of Done asks for measured numbers, not guesses.

- [ ] Measure the time from launch to "ready" in text mode.
- [ ] Measure idle RAM and CPU after 1 minute of waiting, in both modes.
- [ ] Record the numbers in `docs/PERFORMANCE.md`, the baseline for future phases.
- [ ] Update `README.md`: text mode, `apps.json`, the command list and setup steps.
- [ ] Commit and tag: `v2.0-phase1`

**Done when:** every box below is ticked.

---

## Phase 1 is complete when
- [x] Voice and text modes both work.
- [x] KOVA starts and runs local commands without a Gemini key.
- [x] Apps can be opened, closed (after confirming), restarted and listed.
- [x] CPU, RAM and disk are reported on demand, with no background polling.
- [x] `pytest` passes.
- [x] No secrets are in git.
- [ ] Startup time and idle RAM/CPU are measured and written down.
