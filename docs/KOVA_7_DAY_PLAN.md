# KOVA 2.0 — 7-Day Build Plan

**Window:** Day 1 = Tue 3 Nov 2026 → Day 7 = Mon 9 Nov 2026
**Goal:** turn KOVA from a voice command script into a real desktop AI agent. It should understand natural language, use tools safely, remember you, manage your PC, help with coding and gaming, and have its own dashboard.

## Ground rules for the 7 days
1. **KOVA works at the end of every day.** Every day ends with a working, tested, committed version. If a day runs late, we cut features, not stability.
2. **Claude builds, you test.** Claude writes the code and the automated tests and explains the logic of each module briefly. You test the voice and real-PC parts (the mic, speakers, apps and games are on your machine), then we commit.
3. **The roadmap rules still apply:** Gemini only when needed, no background polling, approval before risky actions, and no secrets in git.
4. **Honest scope:** all 10 roadmap phases get a *working first version*, except Phase 9 and parts of Phases 8 and 10, which go in the "after day 7" list. Polishing everything is not possible in 7 days.

---

## The big idea that makes 7 days possible
On **Day 2**, every KOVA ability becomes a **tool** in one tool registry: open an app, check the CPU, search files, start gaming mode, and so on. Each tool has a name, a description, its arguments and a safety level.

- Simple, exact commands ("open chrome") match locally, so they're instant, free and work offline.
- Anything else goes to Gemini with the list of tools. Gemini picks the tools and fills in the arguments, e.g. *"close everything except VS Code and tell me how much RAM I saved"*.
- Every tool call passes through the **permission manager** (safe / controlled / critical) before it runs.

From Day 3 on, every new feature is just another set of tools, so each day adds abilities instead of rewiring KOVA.

---

## Day 1 (Tue 3 Nov): Foundation — roadmap Phase 1 ✅ DONE
**Added after voice testing:**
- The assistant's name is now **Max** (`ASSISTANT_NAME`, `WAKE_WORDS`), because "KOVA" was misheard.
- Stay-awake mode with a sleep timeout (`AWAKE_TIMEOUT`).
- Recording stops when you stop talking, and speech detection uses only the 1–4 kHz band, to ignore fan hum.
- `--mic-test` mode.
- Every installed Start-menu app can be opened.
- Media keys: volume, pause, next.
- Gemini is told it cannot perform actions.

**Remaining from Phase 1, moved to Day 7:** measuring idle RAM/CPU and updating the README.

*Detailed checklist: [PHASE1_PLAN.md](PHASE1_PLAN.md), Milestones 0–4.*
- Tidy up and commit the voice-handler work.
- Command router, with text mode (`python main.py --text`) and voice mode.
- App control: open, close, restart and list apps, configured in `apps.json`.
- On-demand CPU, RAM and disk checks with `psutil`.
- Fuzzy matching, which fixes the broken `play` command.
- Offline voice fallback (`pyttsx3`), logging, and `pytest` tests.

**Demo at end of day:** type or say "open notepad", "system status", "play pasoori" and "close notepad" (it asks first), all without a Gemini key.

## Day 2 (Wed 4 Nov): AI Brain 2.0 + safety — roadmap Phase 3 and the Safety model
- `tool_registry.py`: Day 1's commands become registered tools.
- `ai_brain.py`: Gemini function calling over the registry, with timeouts, retries and clear failure messages.
- Multi-step plans: KOVA shows its plan ("1. close Chrome 2. open VS Code…") before running anything that isn't safe.
- `permission_manager.py` + `permissions.json`: the safe/controlled/critical levels, with the option to always allow a specific action.
- Short conversation context, so follow-ups like "and close it too" work.
- Rule: KOVA never runs shell commands written by the AI. Only registered tools with checked arguments.

**Demo:** "I'm done for today, close my browsers and tell me my battery and RAM".

## Day 3 (Thu 5 Nov): Memory + System Intelligence — roadmap Phases 4 and 2
- `memory_engine.py` (SQLite):
  - preferences ("call me Commander", "my favourite song is…");
  - facts ("remember my project folder is D:\Coding Arc");
  - an activity history;
  - review, correct and delete commands ("what do you remember about me?", "forget that").
- Relevant memories are added to Gemini requests. Secrets are never stored.
- System tools:
  - list the top processes by CPU or RAM; end a process (critical: asks right before);
  - a startup-apps list (view only);
  - power-plan switching, which can be restored;
  - a "health report" (CPU, RAM, disk, battery, uptime, heaviest apps).

**Demo:** "remember I like lo-fi when coding", then restart KOVA → "what do you know about me?" → "why is my laptop slow?"

## Day 4 (Fri 6 Nov): Files & Developer Intelligence — roadmap Phase 6
- File tools:
  - search by name, type or date;
  - open a file or folder;
  - create and rename files;
  - move and organise files, e.g. "organise my Downloads by type". This is controlled, with a preview and an undo log.
- Delete moves files to the Recycle Bin only, never deletes permanently, and is critical.
- Projects: recognise a project folder, open it in VS Code, summarise it.
- Git: status, recent commits, a draft commit message; commit and push only after you confirm.
- Run a project's tests and explain failures; explain a Python file or an error message with Gemini.

**Demo:** "open my KOVA project in VS Code, what changed since yesterday, and run the tests".

## Day 5 (Sat 7 Nov): Desktop Automation + Gaming — roadmap Phases 5 and 7
- Windows: list, focus, minimise, maximise and snap them (`pygetwindow`).
- Workflows in `workflows.json`: one phrase runs several steps, e.g. "coding mode" opens VS Code, Spotify lo-fi and the project folder. It stops safely if a step fails.
- Type text or press keys in the active window, only for configured workflows (`pyautogui`, loaded only when used).
- Gaming mode:
  - detect configured games by process name, and launch Steam games by name (`steam://` links);
  - switch to the high-performance power plan;
  - *suggest* background apps to close (never kills them automatically);
  - restore everything when you exit.

**Demo:** "gaming mode, launch <your game>" → play → "I'm done gaming" → the settings are restored.

## Day 6 (Sun 8 Nov): Desktop Dashboard + Tray — roadmap Phase 10 (core)
- A `PySide6` desktop app:
  - a chat panel (type or press the mic button);
  - live CPU/RAM/disk cards, refreshed every ~3 seconds only while the window is open;
  - an approval pop-up for controlled and critical actions;
  - an activity log and a memory viewer.
- A system tray icon: show/hide, quick mic, quit.
- Voice and dashboard share the same agent core, so a command works the same from either one.
- **Fallback:** if PySide6 has problems on Python 3.14, we switch to CustomTkinter on the same day.

**Demo:** the full KOVA window: talk to it, watch the stats, approve an action from the pop-up.

## Day 7 (Mon 9 Nov): Vision, hardening & release — roadmap Phase 8 (lite) + Definition of Done
- "Look at my screen": a screenshot taken only on command, sent to Gemini to explain an error or what's on screen.
- Better speech: adjust to background noise once, and an optional push-to-talk hotkey instead of always listening.
- Hardening:
  - go through the roadmap's Definition of Done checklist;
  - test with the internet off;
  - test with no Gemini key.
- Measure startup time and idle RAM/CPU and record them in `docs/PERFORMANCE.md`.
- README rewrite and a one-click `run_kova.bat`; optionally package it as an `.exe` with PyInstaller.
- Tag `v2.0`.

**Demo:** the full showcase of everything from Days 1–6, plus "what's this error on my screen?"

---

## After day 7 (backlog)
- **Phase 9:** specialised sub-agents, scheduled and event-triggered tasks.
- Plugin architecture and customisable profiles UI.
- Camera input, offline speech recognition (Vosk), wake-word engine.
- GPU and temperature monitoring (Windows data is unreliable without vendor tools).
- Mobile companion / remote access.

## Daily routine
1. **Start:** Claude restates the day's goal and the logic of each module (≈5 min to read).
2. **Build:** Claude writes the code and tests, then runs the automated tests.
3. **You test:** voice, real apps, games. You report what broke.
4. **Fix**, then **commit** with your OK, then tick the day's boxes in this file.

## Risks
| Risk | Plan |
|---|---|
| Gemini quota/rate limits on the free tier | Local commands first; cache answers; short prompts |
| PySide6 on Python 3.14 | Fall back to CustomTkinter |
| Speech recognition mishears | Fuzzy matching, a text mode that's always available, push-to-talk |
| A day overruns | Move its "nice to have" items to the backlog and keep the day's demo working |
