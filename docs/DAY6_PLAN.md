# Day 6 plan: dashboard + natural voice 🎙️🖥️

**Goal:** one Max window — chat, mic, live trace, approval pop-ups, system stats, a memory viewer and
settings — plus a system tray icon and a push-to-talk hotkey, all served by the **same Agent** the text and
voice modes already use.
**Roadmap phases:** 8 (dashboard) and 10 (natural voice).
**Scope:** Core (steps 1–8) only. The Gemini Live API (Advanced) starts only after Core is built, tested and
Bilal has tried it.

## What is already there

| Already there (Day 1–5) | New today |
|---|---|
| `Agent.confirm` / `agent.ask` / `agent.trace` callbacks | a UI thread + one worker thread sharing them safely |
| CustomTkinter 6.0, Pillow 12.3 | pystray 0.19.5 + pynput 1.8.2 (installed now, added to `requirements.txt`) |
| `tools.save_setting()` keeps every other key in `config.json` | a settings form (voice, alerts, wake words, PTT hotkey) built on it |
| `performance_monitor` reads CPU, RAM, battery, GPU on demand | a sampler thread that runs **only while the window is visible** |
| `forget_memory` tool (Controlled, with approval) | a memory list that deletes through that tool, never by editing files |
| `main.py --text` / voice mode | `main.py --ui` |

## Rules I keep (from DAY6_HANDOFF.md)
- One Agent; requests run on **one worker thread**, one at a time, so the window never freezes.
- Tests stay off real windows, files, OCR, screen and network: widgets are thin, logic is tested on its own
  with fakes (a fake `Bridge`, fake tray/hotkey backends, a fake dashboard).
- No polling when nothing is shown: stats, hotkey and trace pump live only while the UI runs.
- Settings writes go through `tools.save_setting`, so no other key is lost and nothing is printed.
- Never touch or print `config.json` API keys; `config.json.example` gets the new keys.
- Naming: KOVA / Max. No new confirmations. No commits.

## Build order (full suite after every step: `PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider`)

### 1. `ui/bridge.py` + `ui/controller.py` — one agent, one worker thread
- `Bridge`: a queue that carries work from the worker thread to the Tk thread (`post()` from anywhere,
  `drain()` on the Tk thread, `close()` drops everything and releases anyone waiting).
- `Controller`: owns the single worker thread and a FIFO of requests; exposes
  `submit(text)` (typed), `push_to_talk()` (mic/PTT job), and the three agent callbacks:
  - `confirm(question)` / `ask(question)`: post a prompt to the Tk thread and **block the worker** until it is
    answered; `None`, an empty answer or a closed window is always **No**;
  - `trace(line)`: non-blocking, lands in the trace panel.
- **Test:** fake agent + fake dialogs — requests run one at a time and in order, confirm blocks until the
  thread answers, a confirm pending at shutdown returns False instead of hanging, trace lines arrive through
  the bridge.

### 2. `ui/settings.py` — voice, alerts, wake words, PTT hotkey
- Pure form logic: parse the fields → only the keys that actually changed; save each with
  `tools.save_setting` (other keys untouched); apply live: `voice_handler.set_voice`, alerts start/stop,
  wake words rebuilt into the agent's command table, hotkey re-registered.
- **Test:** tmp `config.json` (conftest already points `tools.CONFIG_FILE` at a temp file) — saving changes
  only the given keys and keeps an API key line byte-for-byte; unchanged fields are not written; bad wake
  words fall back to the old list; the agent really matches the new wake word after applying.

### 3. `ui/stats.py` — CPU, RAM, battery, GPU every 2 s, only while visible
- `collect()` formats one reading (GPU through `performance_monitor.gpu_usage`, no guessing).
- `StatsCollector`: a thread that samples every 2 s and pushes to the UI; `start()` on show, `stop()` on
  hide — after `stop()` no thread exists at all (idle CPU stays ~0).
- **Test:** fake sampler/pusher — one sample per tick, stop ends the thread, collect() formats each figure
  and copes with no battery / no GPU.

### 4. memory viewer logic (`ui/memory.py`)
- List facts (`memory_engine.all()`), delete one through the **`forget_memory` tool** via
  `agent.runner.prepare → agent.confirm (pop-up) → runner.run`, so permissions and the action log apply.
- **Test:** real Agent + fake provider + temp DB — the list shows the facts, deleting asks once, "no" leaves
  the fact, "yes" removes it, an unknown number is refused.

### 5. `ui/tray.py` + `ui/hotkey.py` — tray icon and push-to-talk
- Tray: Show/Hide dashboard, Mute mic, Quit (pystray on its own daemon thread; the icon is drawn with
  Pillow, no image file).
- Hotkey: `PTT_HOTKEY` (default `Ctrl+Alt+M`) parsed into pynput's format, on a `GlobalHotKeys` listener
  thread; pressing it does exactly what the mic button does; muted = it does nothing.
- Both libraries are imported lazily and injected, so tests never install a hook or show an icon.
- **Test:** with fakes — menu items call the right callbacks, hotkey string parses (`ctrl+alt+m`,
  `f9`, case/space insensitive), listener starts and stops, no real hook is ever installed.

### 6. `ui/app.py` — the CustomTkinter window (thin widgets, logic above)
- Tabs: **Chat** (log + text box + mic button), **Trace** (fed by `agent.trace`), **Memory** (list + delete),
  **Settings** (form from step 2). Stats bar on top, refreshed every 2 s only while the window is visible.
- Approval pop-up: `CTkToplevel` with the question and **Yes / No**; the window's X is No; `ask()` gets a
  text box with OK/Cancel, X = None. Both are shown through the bridge, so they work while a request runs.
- Closing the main window hides it to the tray; Quit in the tray really stops Max.
- **Test:** import smoke test (no window is created in tests) plus the extracted decision helpers
  (chat line formatting, dialog answer → bool/None).

### 7. `ui/run.py` + `main.py --ui` — wiring
- `run_ui(agent)`: configure voice, point `agent.confirm/ask/trace` at the controller, start tray + hotkey +
  alerts (if `ALERTS_ON`), run the main loop, then tear everything down; returns the `Result` so
  `main()`'s existing `restart yourself` loop keeps working.
- Mic/PTT answers are **spoken**, typed ones are shown only (same rule as voice vs text mode).
- **Test:** fake dashboard/tray/hotkey — callbacks are wired to the one agent, one worker serves typed and
  mic input, teardown stops everything and resets `alerts.on_alert` / `reminders.on_fire`.

### 8. Files and docs
- `requirements.txt`: `pystray`, `pynput`. `config.json.example`: `PTT_HOTKEY`, `ALERTS_ON`.
- `docs/KOVA_7_DAY_PLAN.md`: Day 6 marked **BUILT** with a short note.
- Full suite green, then stop for Bilal's test (no commit).

## Bilal's test list (after Core)
1. `python main.py --ui` → window opens; type a command → chat + trace update; mic button → spoken answer.
2. Ask for something risky → Yes/No pop-up; close it with X → Max cancels.
3. Stats move every 2 s while visible, stop when hidden to the tray; tray Show/Hide, Mute, Quit work.
4. `Ctrl+Alt+M` from any window records one phrase (muted = nothing).
5. Memory tab: list, delete one (pop-up first), the fact is really gone.
6. Settings: change voice/alerts/wake words → saved, other config keys untouched, new wake word works.
7. `--text` and voice mode behave exactly as before.
