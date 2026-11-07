# Day 6 handoff: dashboard + natural voice (for another coding agent)

You are continuing **KOVA**, a Windows desktop voice assistant named **Max**, written by Bilal. Days 1–5 are
done and pushed. Your job is **Day 6** from `docs/KOVA_7_DAY_PLAN.md`. Read that file's Day 6 section and
this one before writing code.

## The project in one minute
- Python 3.14 on Windows 10. Run with `python main.py` (voice) or `python main.py --text` (typing).
- `desktop_agent.py` → `Agent`: one core for everything. `agent.handle(text)` returns a `Result`
  (`modules/result.py`: `success`, `message`, `detail`, `stop`, `sleep`, ...). Simple commands run locally
  (regex table in `Agent.__init__`); longer requests go to the AI brain through `modules/agent_loop.py`.
- The agent talks to the outside only through callbacks you can replace:
  - `agent.confirm(question) -> bool` (approvals; must return False for anything that isn't a clear yes)
  - `agent.ask(question) -> str | None` (follow-up questions)
  - `agent.trace(line)` (the live trace of tool calls: lines like `  → close_app(name='discord')`)
  - see `main.py` `run_text()` / `run_voice()` for how text and voice mode wire them, and `respond()`.
- Brains (`brain/`): Gemini first, the KOVA server (Ollama on Bilal's old laptop) as backup.
  `brain/hybrid.py` decides. Don't change brain logic for Day 6 unless a step needs it.
- Voice in/out: `modules/voice_handler.py` (Google speech recognition, edge-tts voices).
- Memory: `modules/memory_engine.py` (+ `remember`, `recall`, `forget_memory` tools in `modules/tools.py`).
- System figures: `modules/performance_monitor.py`.
- Settings live in `config.json` (gitignored, has API keys — **never print, log or commit it**).
  `config.json.example` lists every setting; add new ones there.

## Day 6 to build, in this order
**Core**
1. **Dashboard window** in `ui/` (new folder). Use **CustomTkinter** (already installed). PySide6 is not
   installed; don't add it unless CustomTkinter can't do something.
   - chat panel (what you said, what Max said) + a text box + a **mic button** (push to talk once)
   - **live agent trace** panel fed by `agent.trace`
   - **approval pop-ups**: a dialog with the question and Yes / No, used as `agent.confirm`;
     closing the dialog = No
   - system stats (CPU, RAM, battery, GPU) refreshed every 2 s **only while the window is visible**
   - memory viewer: list facts, delete one (through the existing `forget_memory` tool, not by editing files)
   - settings: voice male/female, alerts on/off, wake words — saved to `config.json` without losing
     other keys or reformatting it badly
2. **System tray icon** (show/hide dashboard, mute mic, quit) and a **push-to-talk hotkey**
   (e.g. Ctrl+Alt+M; configurable as `PTT_HOTKEY`). Check which library is installed before adding one;
   prefer one small dependency (e.g. `pystray` + `pynput`) and add it to `requirements.txt`.
3. `python main.py --ui` starts the dashboard. Voice, text and UI must share the **same Agent**:
   one agent, run requests on a worker thread so the window never freezes; only one request at a time.

**Advanced (only after Core works and is tested)**
4. **Gemini Live API** real-time voice (natural voice, interrupt Max mid-sentence), only while Max is awake,
   behind `VOICE_MODE: "live"` in config. The current voice pipeline stays the default and the fallback.
   Check the current google-genai docs for the Live API model name and audio format; don't guess them.

## Rules (Bilal's, from earlier days — follow them)
- **Tests:** add tests for every new piece under `tests/`. `tests/conftest.py` stops tests from touching
  real windows, files, OCR, the screen and the network — keep it that way (fake Tk where needed or test
  the logic separately from widgets). Run:
  `PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider` — all **1171** current tests must
  still pass.
- **Don't commit or push** unless Bilal asks in that message. Never commit `config.json`, keys, logs.
- **Naming:** everything is **KOVA** (assistant name Max). Never write "Jarvis" anywhere.
- **Fewer questions:** Max must not ask before everyday, undoable actions; only real risk asks
  (delete, force close, push...). Don't add new confirmations.
- **Don't hurt performance:** no polling loops when nothing is shown; idle CPU should stay near zero.
- **Honesty:** never claim an action happened unless its tool succeeded (see `agent_loop.unbacked_claim`).
- Code style: match the surrounding code — short functions, plain names, comments that say *why*.
- Windows paths have spaces (`D:\Coding Arc\...`): always quote them.
- Write a short `docs/DAY6_PLAN.md` first (steps + how you will test each), then build step by step,
  running the tests after each step.

## Done means
- `python main.py --ui` opens the dashboard; typing and the mic button both work; the trace shows tool
  calls live; an approval appears as a pop-up; stats update; tray + hotkey work.
- Text and voice modes still work exactly as before.
- All tests pass, new ones included. `docs/KOVA_7_DAY_PLAN.md` Day 6 marked BUILT with a short note.
