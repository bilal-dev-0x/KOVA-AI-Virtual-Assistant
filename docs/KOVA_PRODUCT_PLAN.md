# KOVA product plan: audit and design (prompt.txt, Prompt 2)

Written 8 Nov 2026. This document records the audit and staged product plan; implementation status is tracked in section 8 and the phase reports.
Toolkit used: Anthropic **frontend-design** guidance (read in full and applied below); **Playwright** with the
installed Edge for page screenshots. Taste was dropped (Bilal, 8 Nov); Visual Eyes was not installed (its installer
pipes a downloaded script into bash) and is replaced by the same Playwright screenshot loop.

## 1. Audit of the existing project

### What works (tested, 1349 automated tests, live-tested 7 Nov)
- **Agent core** (`desktop_agent.py`, 1,660 lines): 104 local commands (regex table, many with several phrasings each) for apps, windows,
  tabs, files, media, system figures, reminders, routines, games, developer tasks; anything longer goes to the AI.
- **Controlled tool registry** (`modules/tool_registry.py`, `modules/tools.py`): 82 registered tools with
  SAFE / CONTROLLED / CRITICAL levels, argument checks, `permissions.json`, and every run written to
  `logs/actions.jsonl`. Approvals go through `agent.confirm`. No shell commands are ever run.
- **Brains** (`brain/`): Gemini first, the KOVA server (Ollama, `kova-cmd` acts, `kova-chat` talks) as backup;
  claim guard stops answers that say something happened when no tool did it.
- **Voice** (`modules/voice_handler.py`): Google speech recognition, edge-tts voices (male / female),
  wake word, awake timeout, cut-off sentences, "say again".
- **Screen** (`modules/screen.py`, `modules/ocr.py`): screenshots read by Gemini, or by Windows OCR + server.
- **Memory** (`modules/memory_engine.py`): facts the user approved, recall, forget.
- **System** (`modules/performance_monitor.py`, `system_info.py`): CPU, RAM, disk, battery, GPU, top processes.
- **Apps and files**: Start-menu app discovery (`system_control.start_menu_apps`, 166 apps found), launch;
  file search (`files.find`), open, create, rename, delete to the Recycle Bin with approval.
- **Dashboard** (`ui/`): pywebview window built by OpenCode (`ui/web/`, `ui/web_app.py`) on top of the
  Controller (one worker thread), Bridge, VoiceLoop (always-on voice), tray, hotkey `Ctrl+Alt+M`.

### Incomplete or placeholder
- The web dashboard has Home, Assistant, Applications, Files, System, Activity, Memory and Settings.
- **Activity** reads the local `logs/actions.jsonl`; the Home panel continues to show this session's live tool trace.
- **Conversation history** is now kept locally between runs (Phase B); it retains up to 500 messages and
  skips exchanges detected as containing credentials.
- **Personas**: only the voice switches male/female; no Max/Mira profile, no wording or avatar change.
- **Mascot**: not used anywhere in the app. Only `design/mockup_v1/` (a static page) shows it.
- **Floating companion**: not built.
- **Speaking state**: the TTS layer does not report when Max starts and stops talking.
- Bilal rejected the current dashboard look (7 Nov): glowing, generic.

### Known bugs and limits
- Gemini's free tier (100 requests a day) runs out in an evening; the server then answers in 10-80 s.
- Google speech gives no confidence score most of the time (now logged as "?", not a fake 0.50).
- Notepad: typed text lands where the cursor is (start of an opened file).
- The laptop runs at 70-90 % RAM; Claude Code twice stopped the dashboard for low memory.

## 2. Current architecture
```
 voice (VoiceLoop) ─┐                      ┌─ local command table ─┐
 text (web page) ───┼─> Controller ─> Agent.handle ─┤                       ├─> ActionRunner ─> tools ─> Windows
 hotkey / tray ─────┘   (1 worker)                  └─ agent_loop + brain ──┘   (permissions, log)
        ▲                                                   │
        └──────── Bridge (queue) <── trace / confirm / ask ─┘
 pywebview window: index.html + style.css + app.js  <── evaluate_js ──  ui/web_app.py (Api, pump thread)
```

## 3. Proposed final architecture
Keep everything above. Only the window layer grows:
- **One page, eight screens**, plain HTML/CSS/JS (no framework). `app.js` split into one small file per
  screen under `ui/web/screens/`, loaded with `<script>` tags; a tiny router shows one screen at a time and
  loads its data only when it is opened.
- **Api** (`ui/web_app.py`) gains read methods: `apps(query)`, `files(folder | query)`, `system()`,
  `processes()`, `activity(page)`, `history(page)`, `persona()`. Reads never change anything.
- **Every action still goes through the agent's tools** (`runner.prepare` -> permission -> `runner.run`),
  also when it starts from a button: "Launch" calls `open_app`, "Delete" calls `delete_items`. A button never
  calls Windows directly, so approvals, argument checks and the action log stay the same.
- **New backend pieces** (small, tested): `modules/history.py` (conversation kept in `data/history.jsonl`,
  last 500 lines), `modules/personas.py` (`personas/max.json`, `personas/mira.json`), a `speaking` flag around
  `voice_handler.speak`, `files.list_folder(path)` with the same place checks as `files.find`.
- **Floating companion**: a second pywebview window (frameless, always on top, about 280x72). Windows 10
  gives frameless windows square corners and transparency is unreliable, so the pill is an opaque dark
  rectangle with a 1 px border. Click expands to the main window.

## 4. Screen-by-screen plan
Design tokens (from prompt.txt, followed exactly): background `#10141D`, surface `#191F2B`, accent `#26C6DA`,
text `#E6EAF0`, secondary `#8994A5`, success `#36C98F`, warning `#E8B45B`, danger `#ED6472`.
Type: **Segoe UI Variable** (the Windows system face, so KOVA reads as a native app, not a web template),
one family, sizes 12 / 14 / 16 / 22, weights 400 / 600. No all-caps labels, no tracked-out eyebrows, no
middle-dot meta strings, no arrows on buttons (the frontend-design list of generated-page tells).
Layout: a 200 px left rail (screen names and the persona switch), content area, optional right column on wide
windows; below 1000 px the right column moves under the content. Cyan only marks: current screen, live state,
primary button. Errors use danger text, not red boxes.

| Screen | Purpose | Real data / action | Empty / error state |
|---|---|---|---|
| Home | Max is here, what is happening now | mascot (state), greeting, 4 quick actions (each a real command), live CPU/RAM/disk/battery, last 5 actions | "Nothing done yet today" |
| Assistant | Talk to Max | history from `data/history.jsonl`, text box, voice on/off, thinking / working lines from the trace, approval sheet | brain unavailable: says which one and why |
| Applications | Find and start apps | Start-menu apps, search, Launch (via `open_app`), running yes/no | "No app called X" |
| Files | Find and open files | known folders, folder list, search, details (size, date), Open, Show in Explorer, Delete (approval) | blocked folders say why |
| System | How the PC is doing | CPU, RAM, disk, battery, GPU, top 8 processes; sampled every 2 s only while open | a figure Windows doesn't give shows "Not available" |
| Activity | What Max did | `logs/actions.jsonl` newest first: time, tool, result, approved/declined, undo where possible | "No actions yet" |
| Memory | What Max remembers | facts list, Forget (approval); explains that facts are stored on this PC only | "Max doesn't remember anything yet" |
| Settings | How Max behaves | persona, voice, wake words, hotkey, start with Windows, brain status (key present: yes/no, never the key), alerts, permissions list | — |

## 5. Mascot state plan (Max and Mira)
Asset facts: one idle loop (`reaper_idle.mp4`, 480x480, 8 s, about 1 MB after re-encode) and one still
(Gemini picture). Background `#131722`, close to the surface colour, so it sits in a frame of that colour.
No new art is invented; states are shown by **what plays and one small change**, not new animation:

| State | Real trigger | Shown as |
|---|---|---|
| Sleeping | voice asleep / window idle | still image, dimmed to 70 %, no motion |
| Idle | awake, waiting | idle loop, normal speed |
| Listening | mic hears speech | idle loop + accent ring under the frame |
| Thinking | request sent, no tool yet | loop at 0.6x speed + "Thinking" label |
| Working | a tool is running | loop + the tool's plain name ("Opening Chrome") |
| Speaking | `speaking` flag on | loop at normal speed + 3-bar level line |
| Happy | last result ok, 2 s | brief scale 1.03 and back |
| Error | last result failed | still image + danger label with the reason |
Video pauses whenever the window is hidden or minimised. Mira: same character, warm tint
(`hue-rotate` on the frame, accent unchanged elsewhere), her own name, voice and wording (see
`docs/PERSONA_AVATAR_PLAN.md`).

## 6. Backend integration
Each screen gets exactly one read method and uses existing tools for actions (section 3). New tests:
one per Api method with fakes (no real windows, files or network, as `tests/conftest.py` already enforces);
`history`, `personas`, `list_folder`, `speaking` flag each tested on their own.

## 7. Safety and permissions
Unchanged rule: understand -> plan -> permission -> run a registered tool -> read the result -> answer.
- Buttons use the same tools and the same approval sheet as voice and text.
- Delete, overwrite, move, force close, push: always asked (CRITICAL / CONTROLLED as today).
- File screen shows only allowed roots (`files.allowed_roots`), never Windows or Program Files.
- Settings shows whether a Gemini key exists, never the key; saving keeps other keys untouched.
- Activity log is the existing `actions.jsonl`; nothing extra is stored about the user.

## 8. Implementation phases (Prompt 3)
A. Shell: rail, router, tokens, the eight screens as real routes, responsive rule. Mascot in Home (idle only).
B. Assistant screen: history file, thinking / working / approval / error states. **Done 8 Nov 2026** — see
   [`PHASE_B_REPORT.md`](PHASE_B_REPORT.md).
C. Applications, Files, System, Activity with real data. **Implemented 8 Nov 2026** — see
   [`PHASE_C_REPORT.md`](PHASE_C_REPORT.md). Native screenshot review remains in Phase F.
D. Mascot states, `speaking` flag, Max/Mira personas, floating companion. **Done 8 Nov 2026** — see [`PHASE_D_REPORT.md`](PHASE_D_REPORT.md).
E. Settings (persona, start with Windows, brain status), first-launch screen when no key is set.
F. Playwright and native screenshots of every screen, top five problems fixed, re-shot; CPU/RAM measured.
Each phase ends with all tests passing and screenshots; Bilal is asked before the next phase.

## 9. Risks and dependencies
- **RAM**: WebView2 adds 60-100 MB, a second window for the pill adds more; pill window is created only
  when used.
- **Gemini quota**: Assistant tests may fall back to the slow server; tests use fakes, live checks are few.
- **pywebview multi-window** on Python 3.14 must be checked early in phase D; fallback: the pill is the main
  window shrunk.
- **Video codec**: WebView2 plays H.264 MP4 (checked with Edge); no extra codec needed.
- **Start with Windows**: a shortcut in the user's Startup folder (no admin, no registry).
- No new heavy dependencies: pywebview, pystray, pynput already installed; Playwright is dev-only.
