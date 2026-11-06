# KOVA 2.0 — 7-Day Build Plan (ambitious version)

**Window:** Day 1 = Tue 3 Nov 2026 → Day 7 = Mon 9 Nov 2026
**Goal:** turn KOVA, whose assistant is called **Max**, into a real desktop AI agent. Max should:
- plan and use tools;
- remember you and watch out for problems;
- see and operate your screen;
- help you code and game;
- talk to you in natural real-time voice, with a dashboard.

## Ground rules
1. **Max works at the end of every day.** Each day ends tested, committed and pushed. If a day runs late, we drop *Stretch* items first, then *Advanced* ones. *Core* items always ship.
2. **Claude builds, you test.** Claude writes the code and the automated tests and explains each module briefly. You test what needs your PC (mic, apps, games, screen): about 30–60 min a day.
3. **Roadmap rules still apply:**
   - local first, AI on demand;
   - no constant polling or recording;
   - approval before consequential actions;
   - no shell commands written by the AI;
   - no secrets in git.
4. **Brain is swappable.** All AI calls go through one provider interface. We build on **Gemini** now; a **local LLM via Ollama** can be added later as a second provider (see Backlog) without rewriting the agent.

Each day lists **Core** (must ship), **Advanced** (target) and **Stretch** (only if time allows).

---

## Day 1 (Tue 3 Nov): Foundation — roadmap Phase 1 ✅ DONE
- Command router with safety levels; text mode and voice mode.
- Open, close, restart and list apps (`apps.json` plus every Start-menu app); websites.
- On-demand CPU, RAM, disk and battery; media keys; music with fuzzy matching.
- Gemini fallback, which no longer claims to do actions; logging; 130 tests.
- Added after voice testing:
  - the assistant is called **Max**, with configurable wake words;
  - stay-awake mode;
  - recording stops when you stop talking;
  - speech detection in the 1–4 kHz band, which ignores fan hum;
  - `--mic-test`.

Moved to Day 7: idle RAM/CPU measurements and the README.

## Day 2 (Wed 4 Nov): The Agent Brain 🧠 — roadmap Phase 3 + Safety model ✅ DONE
*Built:*
- every Core item;
- Advanced: context, reminders, undo, plugins, live trace, process tools;
- Stretch: budget guard.

*Changed:*
- Google Search grounding has a quota of 0 on the free tier, so `web_search` falls back to opening a browser search, and news falls back to NewsAPI.
- Each free model allows only ~20 requests a day, so Max now switches through a **model fallback chain** (`GEMINI_MODELS`).
- Cancel = say no at the approval, or Ctrl+C.
- Learned phrases and spoken progress move to Day 3.

**Core**
- `tool_registry.py`: every ability becomes a tool with a name, a description, typed arguments, a safety level and whether it can be undone. An `@tool` decorator registers it.
- `brain/` with a **provider interface**; `GeminiProvider` with function calling.
- **Agent loop:**
  1. plan;
  2. call tools and read the results;
  3. continue or answer.
  
  It stops after 6 steps. Every number in an answer comes from a tool.
- **Hybrid routing:** exact commands stay local (instant, free, offline); natural or complex requests go to the agent.
- `permission_manager.py` + `permissions.json`:
  - safe actions run;
  - controlled actions get **one approval for the whole plan**;
  - critical actions ask again right before each step;
  - optional "always allow" for specific actions.
- `logs/actions.jsonl`: every tool call with its arguments, approval and result.
- Safety: there is no shell tool; arguments are validated against the schema; targets are checked before acting.

**Advanced**
- **Google Search grounding** for live answers (weather, news, prices). It replaces NewsAPI.
- **Conversation context:** "open discord" → "actually close it".
- **Reminders and timers:** "remind me in 20 minutes…". They use a sleeping background timer, with no polling.
- **Cancel and undo:** "cancel" stops a plan; "undo" reverses the last action that can be undone.
- **Plugins:** a `.py` file in `plugins/` with `@tool` functions is loaded automatically at startup.
- **Live agent trace** in the terminal (→ tool → result → next step).
- **Process tools**, pulled forward from Day 3: top processes by CPU/RAM, "why is my laptop slow?".

**Stretch**
- **Learned phrases:** when Gemini maps a new phrase to a tool, Max caches it, so next time it's instant and offline.
- **Gemini budget guard:** a daily request limit, with a warning near the free-tier quota.
- **Spoken progress** during long plans.

**Demo:** *"My laptop feels slow, close whatever is wasting RAM except VS Code, then tell me how much I saved."*

## Day 3 (Thu 5 Nov): Memory + a Max that keeps watch 🧩 — roadmap Phases 4 and 2 ✅ DONE
*Plan and checklist: [DAY3_PLAN.md](DAY3_PLAN.md).*

*Built:*
- every Core item;
- Advanced: auto-memory, activity recall, alerts, routines;
- extra, from the voice tests: open a folder by memory or by voice ("open my project folder"),
  close terminal windows (never Max's own), forget by number.

*Changed:*
- **Meaning-based recall is offline** (stems, a synonym table, near spellings). Gemini embeddings were left out:
  the free key allows ~20 requests a day and the relay brain has none.
- This PC has only the **Balanced** power plan, so the example "coding mode" has no power-plan step.
- Stretch (Max suggests a routine) moved to Day 4's Stretch.

*Tested:* voice tests of steps 1–2 (5 Nov) and 3–8 (6 Nov), then a text test of 66 hard commands (6 Nov).
It found a "wifi pin" being saved, a "+92" phone number refused as a card number, "this week" read as the last app,
"remember ... and open them" saving the action as part of the fact, a wrong answer for a routine that doesn't
exist, and "in last week". All fixed; 741 tests.
Commits: `b6699f2`, `8a92035`, `d2365b8` (the last three fixes are not committed yet).

**Core**
- `memory_engine.py` (SQLite): facts, preferences, an activity history.
- Memory review, correction and deletion: "what do you remember about me?", "forget that".
- **Meaning-based recall** with Gemini embeddings: "what's my project folder?" finds a fact saved in different words.
- Relevant memories are added to agent requests. Secrets and passwords are never stored.
- System intelligence:
  - startup apps (view);
  - power-plan switching, which can be restored;
  - a health report (CPU, RAM, disk, battery, uptime, heaviest apps).

**Advanced**
- **Auto-memory:** Max notices facts worth keeping and *asks* before saving them.
- **Activity recall:** "what did I do yesterday?", "which apps did I use most this week?"
- **Proactive alerts**, opt-in and checked every 30–60 s: battery low, RAM above 90% (naming the culprit), disk almost full.
- **Routines** (`routines.json`): "coding mode", "morning routine". Each is a multi-step plan that stops safely on failure.

**Stretch**
- Max **suggests** a routine after seeing you repeat the same steps.

**Demo:** "remember I like lo-fi when coding" → restart Max → "what do you know about me?" → "coding mode".

## Day 4 (Fri 6 Nov): Eyes and hands 👁️✋ — roadmap Phases 5 and 8 🔨 BUILT, your test pending
*Today's plan, checklist and your test list: [DAY4_PLAN.md](DAY4_PLAN.md).*

*Built (not committed yet):*
- every Core item and both Advanced items;
- each step was reviewed by a second Claude session, and all its findings were fixed.

*Not done:* Stretch (moved to Day 5).

*Changed from the plan:*
- Organising is limited to Downloads, Desktop and Documents.
- Risky buttons (Delete, Send, Pay...) ask again.
- A screenshot is taken with `PrintWindow`, so windows lying on top aren't in it.
- Found and fixed along the way: an approval answered with neither yes nor no let a step run.

929 tests. The live Gemini screenshot (quota) check is the first item of your test.

**Core**
- **Screen understanding:** "what's this error?", "summarise this page". A screenshot is taken **only on command**,
  of the active window unless you say otherwise, kept in memory only, and sent to the brain
  (Gemini with an image; the relay brain gets a file that is deleted after the answer).
- **Window control:** list, focus, minimise, maximise, snap, arrange ("VS Code left, Chrome right"), with undo.
- **File tools:**
  - find, open, create and rename, only in your own folders and non-system drives;
  - programs and scripts are never opened;
  - organise Downloads, with a preview and an undo log that survives a restart;
  - delete moves to the Recycle Bin only (at most 20 files) and counts as critical.

**Advanced**
- **Windows UI Automation:** read the real buttons, menus and fields of any app, then click and type **by name** ("click Save", "type hello into the search box"). Never types into password fields.
- **Multi-step desktop tasks:** each step is previewed, consequential steps ask first, and Max **stops safely** if something looks wrong (the window changed, the control is missing).

**Stretch**
- Vision + UI Automation together: "click the blue Sign in button" on screens that have no accessible names.
- Carried: Max suggests a routine after the same steps 3 times (Day 3); learned phrases and spoken progress (Day 2).

**Demo:** "what's this error on my screen?" → "put VS Code on the left and Chrome on the right" → "organise my Downloads folder".

## Day 5 (built Fri 6 Nov, evening): Developer + gaming companion 💻🎮 — roadmap Phases 6 and 7 ✅ BUILT, your test pending
*As built, the live demo and your test: [DAY5_PLAN.md](DAY5_PLAN.md).* Games from Steam, Riot, Epic and
the D drive (7 found), gaming mode, projects by their GitHub name, VS Code, tests (KOVA ran its own:
1043 passed), git status and changes, commit and push only after a yes. Not done: per-game profiles,
proposed fixes as a diff.
**Core**
- Projects: recognise a project, open it in VS Code, summarise it.
- Run its tests and explain failures; explain errors (also read from the screen, using Day 4).
- Git: status, a summary of changes, a draft commit message; commit and push **only after you confirm**.
- Gaming mode:
  - detect configured games;
  - launch Steam games by name;
  - switch the power plan;
  - *suggest* background apps to close;
  - **restore everything** afterwards.

**Advanced**
- **Proposed fixes:** Max shows a fix as a diff and applies it only after you approve; a backup comes first.
- **Max works on KOVA itself:** "Max, run KOVA's tests and tell me what failed."
- Steam library awareness: list your installed games.

**Stretch**
- Per-game profiles, e.g. close Discord's overlay and set the volume.
- Carried from Day 4: vision + UI Automation ("click the blue Sign in button"); from Day 3: Max suggests a
  routine; from Day 2: learned phrases and spoken progress.

**Demo:** "open my KOVA project, what changed today, run the tests" → "gaming mode, launch <game>" → "done gaming".

## Day 6 (Sun 8 Nov): Natural voice + dashboard 🎙️🖥️ — roadmap Phases 8 and 10
**Core**
- **Dashboard (PySide6;** CustomTkinter as a fallback):
  - a chat panel and a mic button;
  - the **live agent trace**;
  - **approval pop-ups**;
  - system stats (refreshed only while the window is open);
  - a memory viewer and editor;
  - settings.
- **System tray** icon and a **push-to-talk hotkey**.
- Voice, text and dashboard share one agent core.

**Advanced**
- **Real-time voice with the Gemini Live API:**
  - natural conversation in a natural voice;
  - **interrupt Max mid-sentence**;
  - much better accent handling.
  
  It runs only while Max is awake. The current voice pipeline stays as the offline fallback.

**Stretch**
- Live API + tool calls in the same session: talk and act without switching modes.

**Demo:** the full Max window: talk naturally, interrupt it, approve an action from the pop-up.

## Day 7 (Mon 9 Nov): Multi-agent + release 🚀 — roadmap Phase 9 + Definition of Done
**Core**
- Hardening: the roadmap's Definition of Done checklist, tests with the internet off, tests with no Gemini key.
- Measure startup time and idle RAM/CPU, recorded in `docs/PERFORMANCE.md`.
- README rewrite with a demo script; a one-click `run_max.bat`; tag **v2.0** and a GitHub release.

**Advanced**
- **Specialist agents:** System, Coding, Research and Media, coordinated by a central planner. Long requests are split between them.
- Scheduled and event-triggered tasks, building on the Day 2 reminders and Day 3 alerts.

**Stretch**
- **Phone companion:** commands from your phone over home Wi-Fi, secured with a token and **off by default**.
- A packaged `.exe` (PyInstaller).

**Demo:** the full showcase, ending with a multi-agent request such as *"research a laptop cooling pad under 5000 rupees and remind me tonight"*.

---

## Backlog (after Day 7, or on a day that finishes early)
- **KOVA server brain ✅ BUILT (6 Nov, after the Day 4 live test ran Gemini dry by 1 pm):**
  - `brain/server.py`: the old laptop's Ollama with a new `kova-cmd` model (qwen3 4B instruct, `num_ctx` 6144,
    `think` off), over the cable (`192.168.50.2`) with Tailscale (`100.109.31.1`) as the way round. No model
    runs on this laptop, so the "no local model inference" rule still holds.
  - `brain/hybrid.py`: **Gemini first, the KOVA server as the backup** (`BRAIN_PROVIDER: "gemini"`, the
    default). When Gemini can't answer (daily quota, busy, no internet), the server answers for 10 minutes
    before Gemini is tried again. `BRAIN_PROVIDER: "server"` = the server only; `SERVER_BRAIN: false` = Gemini only.
  - The server gets 22 tools (`SERVER_TOOLS`), not all 66: every description is read with every request.
  - Measured: ~45 tokens/s to read, ~6/s to write. The first answer after the model loads takes 60–90 s
    (2,760 tokens of prompt + tools); after that ~9–10 s, because Ollama keeps what it read. So when
    Gemini runs low (80 % of `GEMINI_DAILY_LIMIT`, or half its models out of quota), Max warms the server
    up once in the background.
  - The screen: Gemini sees the picture. **✅ Since 6 Nov night**, when Gemini can't, the server answers
    from the screen's **text**, read by the OCR built into Windows (`modules/ocr.py`, `winrt-Windows.Media.Ocr`):
    free, offline, ~0.3 s, from the full-size screenshot (2x enlarged). It reads errors, pages and buttons,
    but not pictures, colours or games. Live: a pygame traceback, explained with its fix, in 25 s.
    Hard live test (3 rounds): errors, a busy log, an order table on a dark page, an empty window, a private
    window and a follow-up all right in 15–22 s. Login codes and card numbers are hidden before the text
    leaves the PC; text on screen that gives orders is never acted on (no tool runs while reading, and
    "I will now…" sentences are dropped). The 4B model can still get sums or guesses wrong.
  - Not done, on purpose: the server's older news/weather API, because it loads a second 4B model next to
    `kova-cmd` (8 GB RAM, Bilal's rule: don't slow either PC). Later: rename it to KOVA and let it use
    `kova-cmd`.
- Offline speech recognition (Whisper/Vosk) and an offline wake-word engine.
- Camera input (only with clear activation).
- GPU temperature monitoring (unreliable on Windows without vendor tools).
- Plugin marketplace and profile UI.

## Daily routine
1. **Start:** Claude restates the day's goal and the logic of each module (≈5 min to read).
2. **Build:** Claude writes the code and tests, then runs the automated tests.
3. **You test:** voice, real apps, screen, games. You report what broke.
4. **Fix → commit → push**, with your OK; tick the day's boxes here.

## Risks
| Risk | Plan |
|---|---|
| Gemini free-tier limits: **measured on Day 2 at ~20 requests/day per model, with no search grounding** | Local-first routing, a model fallback chain, a 6-step cap, short tool descriptions, a daily budget guard; later Ollama or a paid key |
| Gemini Live API on Python 3.14 / audio streaming | The current voice pipeline stays as the fallback; Live is an *Advanced* item |
| Screenshots on the free Gemini key (unknown cost per image) | One measured test call on Day 4 before building on it; the relay brain can read pictures too |
| UI Automation fails in unusual apps | Read before acting, stop on mismatch, use vision as a backup |
| UI Automation through comtypes on Python 3.14 | comtypes 1.4.17 is installed; the `uiautomation` package is the fallback |
| server-realm is off, unplugged or busy | Gemini stays the fallback brain; the cable has Tailscale as its own fallback |
| PySide6 on Python 3.14 | Fall back to CustomTkinter |
| More testing for you | Every day keeps a text-mode demo, and automated tests cover the logic |
| A day overruns | Drop Stretch, then Advanced; Core always ships |
