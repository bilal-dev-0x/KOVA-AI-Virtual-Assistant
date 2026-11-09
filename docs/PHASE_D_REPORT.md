# KOVA Phase D report

Date: 8-9 November 2026.

## Built
- **Speaking flag:** `voice_handler.is_speaking()`, set around every spoken line and cleared even when the voice fails. The window shows a "Speaking" status from it.
- **Personas:** `personas/max.json`, `personas/mira.json`, `modules/personas.py`. A persona sets name, voice, wake words and one line of manner for the AI prompt. Safety lines in the prompt are identical for both.
- **Settings:** a Persona switch. Choosing one sets name, voice and wake words together and starts a fresh AI session; the page updates without a restart.
- **Mascot states:** accent outline while listening, warning outline for approval, danger outline for error (loop paused), loop at 0.6x while thinking, three moving bars while speaking, a 2 s scale to 1.03 after a successful answer. Mira is the same Reaper with a warm tint (`hue-rotate`).
- **Floating pill:** `ui/pill.py` and `ui/web/pill.html`, a 300x64 frameless always-on-top window in the bottom-right corner. Created only the first time the dashboard is hidden to the tray; it shows the state and has Talk and Open buttons. If a second window cannot open, there is simply no pill.

- **Home redesign** (Bilal's Gemini mockup, own Reaper clip): a large Reaper on a stage of the clip's own colour with the clip's edge faded out, a plain greeting with a line that follows Max's state, a status pill, a message box on Home, and the PC and session cards on the right. Outline borders on buttons, fields and panels were replaced by filled surfaces; mouse clicks no longer draw a focus ring (keyboard focus still does).
- **Reaper on the desktop** (`ui/pill.py` `Pet`, `ui/web/pet.html`): a round always-on-top window, switched in Settings (`PET_ON`, off by default). Drag to move, double-click opens KOVA. It follows Max's real state while the dashboard is open, maximised, minimised or in the tray: the ring breathes while listening, the Reaper sways while thinking or working, bars move while speaking, a finished job pops and a failed one shows a red ring, asleep is dimmed and still. Windows 10 has no reliable transparent window, so the window is cut to a circle with a window region.

## Checked
- 1385 tests pass. The desktop Reaper was checked on the real desktop above a maximised dashboard, and followed a request (thinking, pop, asleep) while the dashboard was minimised.
- Persona switch to Mira and back was run on the real window (config restored afterwards). The pill was opened for real and photographed.

## Limits
- The window's X button still quits KOVA (as before). The pill appears when the dashboard is hidden from the tray menu, not on X.
- The window title still says "KOVA - Max" after switching to Mira until the next start.
- Listening/thinking/speaking outlines were checked by tests and CSS, not by watching a real spoken request.
- Mira's warm tint is a pink-magenta; it can be tuned in `style.css` (`hue-rotate`).
