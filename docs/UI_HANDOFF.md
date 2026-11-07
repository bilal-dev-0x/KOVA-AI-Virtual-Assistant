# UI handoff (for another coding agent)

Build the new KOVA dashboard. Read first, in order: `docs/UI_DESIGN_PLAN.md`, `docs/PERSONA_AVATAR_PLAN.md`
(avatar only for context, do not build it), then this file.

## Your scope: UI_DESIGN_PLAN steps 1-6 only
Window skeleton and Python-JS bridge, design tokens and sidebar, Home and chat, Activity and the approval
dialog, stat rings, Memory and Settings. **Not** in scope: floating pill, personas, avatar, Gemini Live.

## Facts
- Python 3.14, Windows 10. Run: `python main.py --ui`. Old window stays reachable as `--ui-classic`.
- Keep and reuse: `ui/controller.py`, `ui/voice.py`, `ui/bridge.py`, `ui/settings.py`, `ui/memory.py`,
  `ui/stats.py`, `ui/tray.py`, `ui/hotkey.py`, `ui/run.py`. Replace only the window layer (`ui/app.py`).
  Move the old `ui/app.py` to `ui/classic.py` and wire `--ui-classic` to it.
- New window: **PyWebView** (WebView2), files in `ui/web/` (`index.html`, `style.css`, `app.js`), plain JS, no framework.
  Fonts and icons stored in the project (Inter, Lucide), nothing loaded from the internet. Add `pywebview` to `requirements.txt`.
- The window is shared with the Controller through the Bridge: Tk-free now, so push to the page with `evaluate_js`.
  Only one request at a time; the window must never freeze.

## Rules
- Tests: `PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider`. **All 1264 current tests must pass**,
  new ones added per step. Tests never open a real window (see `tests/conftest.py`, `tests/test_day6_*.py`).
- **Do not commit or push.** Never open, print or change API keys in `config.json`.
- Name everything KOVA (assistant names Max / Mira). Never write "Jarvis".
- Design rules are in the plan: one cyan accent, Inter, 4/8/16/24 grid, one radius, 150-200 ms animation,
  no emoji, no marketing text, only real data (no placeholder cards). It must not look AI-generated.
- Footer shows the real brain ("Gemini" or "KOVA server"). Real date and time.
- No polling when nothing is shown: stats only while the window is visible.
- Do not start the app in the background and leave it running (the laptop is low on RAM). If you open it to
  look, close it again.

## Done means
Steps 1-6 work in `python main.py --ui`, text and voice still work, all tests pass, and a short report says
what was built, how to run it, and test numbers. Stop there; Claude reviews and live-tests.
