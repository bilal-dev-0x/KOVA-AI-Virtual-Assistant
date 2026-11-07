# KOVA dashboard redesign: plan (not built yet)

Reference: `Downloads/MAX_ Neon AI Desktop Assistant UI.png`. Goal: that look, without looking AI-made.
Status: **plan only, nothing implemented.** The current CustomTkinter dashboard (`ui/app.py`, commit `4435577`) stays until this is built and approved.

## 1. Technology
- **PyWebView + Edge WebView2**: a native Python window, the inside is HTML/CSS/JS. WebView2 ships with most Windows 10 installs; check at startup and fall back to the old window if missing.
- **No frameworks** (no React, no Tailwind): plain HTML, CSS, a little JavaScript. Smaller, lighter on RAM.
- **Fonts and icons stored in the project** (Inter, Lucide), nothing loaded from the internet.
- New code lives in `ui/web/` (`index.html`, `style.css`, `app.js`) plus a thin `ui/web_app.py`.
- **Unchanged:** `Controller`, `VoiceLoop`, `settings`, `memory`, `stats`, `tray`, `hotkey`. Only the window layer is replaced, so the existing tests keep working.
- Python <-> JS: buttons call Python functions; Python pushes answers, trace lines and stats into the page with `evaluate_js`.

## 2. Design rules
| Thing | Rule |
|---|---|
| Colour | dark blue-black background, **one** accent (cyan) |
| Font | Inter, three sizes only |
| Spacing | 4 / 8 / 16 / 24 px grid everywhere |
| Corners | one radius everywhere |
| Glass blur | only on the sidebar and the pill, not on every card |
| Animation | 150-200 ms fade/slide only, no bounce, no glow |
| Text | plain ("Listening", "CPU 12%"), no marketing lines, no emoji |
| Data | everything real: real CPU, real tool calls, real memory. No placeholder cards |

## 3. Screens
1. **Home**: greeting, ask box, four quick actions, CPU/RAM/disk rings, Max's state (asleep / listening / thinking).
2. **Activity**: every tool call live, with its result.
3. **Memory**: facts, "Forget" with the usual approval.
4. **Settings**: voice, alerts, wake words, hotkey (theme: dark first).
5. **System**: CPU/RAM/GPU/battery and top processes.
6. **Floating pill**: small always-on-top window, "Max · Listening" and a waveform; click opens the dashboard.

Left out of round one: the mock-up's **Applications** and **Files** pages (they need their own backend, not part of Day 6).

## 4. Changes from the mock-up
- Footer shows the real brain ("Gemini" or "KOVA server"), not "Powered by Gemini".
- Product name KOVA, assistant name Max.
- Real date and time, not the mock-up's.

## 5. Build order (tests after every step)
1. Window skeleton and the Python-JS bridge.
2. Design tokens and sidebar.
3. Home and chat.
4. Activity and the approval dialog.
5. Stat rings (sample only while the window is visible).
6. Memory and Settings.
7. Floating pill, tray and hotkey wiring.
8. Live test with Bilal.

Testing: Python parts tested without a real window (as in `tests/test_day6_*.py`); look checked with screenshots.

## 6. Risks
- Transparent window for the round pill can misbehave on Windows: fall back to a slightly opaque pill.
- Real mic level for the waveform needs `voice_handler` to expose it; if hard, the waveform follows the state only.
- WebView2 costs about 60-100 MB: release it while the window is hidden in the tray.
- Keep the old window as `--ui-classic` until the new one is approved.

## 7. Decisions (made 7 Nov)
1. Old window stays as `--ui-classic` (a backup) until the new one is approved.
2. Dark theme only for now; light later.
3. The avatar (Mini Reaper, personas Max and Mira) is planned in `docs/PERSONA_AVATAR_PLAN.md` and shown in Home and the pill.
