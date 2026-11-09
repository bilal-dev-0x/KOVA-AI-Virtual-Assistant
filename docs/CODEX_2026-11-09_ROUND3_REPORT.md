# Round 3 report — 9 Nov 2026

## What changed

Round 3 work written into the working tree (uncommitted), completed and repaired:

- `modules/scheduled_tasks.py` (B6): list of Windows scheduled tasks as a SAFE tool reading,
  started and stopped from `main.py`; command "list scheduled tasks". Already tested before this round.
- `tests/test_round3_a8_vision_click.py` (A8): the "click by looking" code it tests exists and is real:
  after a UIA miss, `ui_click` in `modules/tools.py` takes a screenshot through `screen.look`
  (never to disk), sends it to a vision-capable brain with a prompt that treats screen text as
  untrusted, and clicks a mapped coordinate with `ui_automation.click_at`. Guards that are in place:
  risky controls (Delete, Send, Pay...) never use the vision fallback; the click needs confidence
  0.75 or higher and normalized 0-1000 coordinates inside the window; a same-window check runs
  right before clicking; a missing or low-confidence answer says so instead of guessing.
- `modules/project_patch.py` + `apply_project_patch` in `modules/tools.py` (B10): one reviewed
  unified diff to one existing project file, previewed before approval, secret-bearing files
  blocked, undo restores the original backup.
- `modules/routine_habits.py`, hooked in `desktop_agent.py` (B13): three same safe steps on three
  days produce one spoken suggestion; explicit "save this routine as <name>" saves it locally.
  Habits never leave the PC.
- `docs/SPECIALISTS_PLAN.md` (B5): design only, needs Bilal's yes before any code.
- Fixes this session (OpenCode):
  - `modules/tool_registry.py`: a blank optional string argument (e.g. `project: ""` meaning
    "the last or KOVA project") no longer raises a spurious "must not be empty" error; it now
    falls through to the tool default. Required blank strings still fail.
  - `desktop_agent.py`: commands now can carry a `decline_text`; the routine-save decline says
    "Okay, I didn't save the routine." instead of the generic "Okay, cancelled." so the answer
    matches what was asked.

## Verification

- **Test count:** `1483 passed, 3 warnings` with `python -m pytest -q -p no:cacheprovider`
  (upstream deprecation warnings from AnyIO, SpeechRecognition and Google GenAI only).
- `node --check ui/web/app.js`: passed.

## Not verified live

- A8 vision click with a real screenshot and brain: the code path and its guards are verified by
  tests only; a live run needs a vision-capable brain in front of the app.
- A5, A6, A9 and B6 were live-checked on this PC through the CDP webview (see below), not by voice.
- Ctrl+Alt+K / Ctrl+Alt+M on a real keyboard, "Start with Windows", and the X-button decision (A10)
  are for Bilal to check himself.

## Live checks (CDP, dashboard at 19:35-20:05 local)

- B6: "list scheduled tasks" answered from the real tool: "You don't have any scheduled tasks."
- A5: with my own Notepad window in front, "open edit" clicked Notepad's Edit control through
  `ui_click` (actions.jsonl: safe, not needed, success: "Clicked Edit.").
- A6: "close notepad" closed a notepad I had started, with no question (log: approval not needed).
  "force close notepad" showed the real approval sheet ("Force close notepad? Anything unsaved
  in it will be lost." with No / Yes, do it); answering No through the sheet replied
  "Okay, cancelled." and the window stayed open.
- A9: switching the persona Max → Mira in Settings renamed the dashboard window title to
  "KOVA — Mira"; reverting restored "KOVA — Max".
- App state afterwards: every app and window used in these checks was closed again; config.json
  was only touched by the two Settings saves that switched the persona out and back.

## Performance (scripts/measure_performance.py, first real numbers)

- Pet off (3 runs): start 1.35/1.33/1.38 s, local answer 9.1/0.69/0.67 ms, idle CPU 4.45/2.50/3.40%,
  private memory 258.0/198.5/212.3 MB.
- Pet on (3 runs): start 1.35/1.45/1.40 s, local answer 6.9/0.70/0.71 ms, idle CPU 2.75/2.70/2.60%,
  private memory 239.6/241.7/240.9 MB.
- `docs/PERFORMANCE.md` now holds these medians; the earlier table rows predated a verified run
  and were replaced.

## Harness note

While driving the approval sheet from Playwright over CDP, two sessions wedged: every further
`pywebview` bridge call from the CDP page hung until the dashboard was restarted. The sheet itself,
its description, and the No-answer behaved correctly both times, and no force close ever ran
without approval (actions.jsonl has no unapproved entry). Normal UI input does not go through the
CDP bridge, but this path deserves a watch if it ever happens at Bilal's desk.

## Next

- Bilal's decision on committing and pushing Rounds 1-3.
- Then, only on Bilal's yes: B4 tag `v2.0` + GitHub release; B5 specialists slice; A10 X-button;
  B7 phone companion; B8 `.exe` packaging; B9 Gemini Live.
