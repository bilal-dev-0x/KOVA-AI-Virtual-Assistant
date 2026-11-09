# Round 2 implementation report — 9 Nov 2026

## What changed

- `desktop_agent.py`: unknown `open` targets can select one exact matching control in the front window; duplicate matches are reported for user choice. Exact app names keep app-opening behavior.
- `modules/tools.py`: gentle `close_app` is SAFE, so it no longer prompts; `force_close` remains CRITICAL.
- `ui/voice.py` and `config.json.example`: approval listening reads `APPROVAL_SECONDS` (default 45) and speaks “I didn't hear a yes, so I left it.” after the approval wait expires.
- `ui/web_app.py`: persona refresh updates the dashboard title and `_title`.
- `modules/agent_loop.py`: a brain connection error produces one short, honest message.
- `README.md` rewritten; `run_max.bat` added; `scripts/measure_performance.py` and `docs/PERFORMANCE.md` added.
- Added nine tests in `tests/test_round2_a5_front_control.py`, `tests/test_round2_a6_approvals.py`, `tests/test_round2_a9_persona_title.py`, and `tests/test_round2_b1_offline.py`.
- Follow-up fixes after the external test run: kept Claude's `SAFE` import in `modules/tools.py`; restored `close_app` to `CONTROLLED` for AI plans, because only the local spoken gentle-close route is prompt-free. The local route passes `not needed` to the ActionRunner; force close and all planned controlled actions still ask.
- Fixed the new front-control test's mock parameter collision and its approval-timeout fixture. The offline error response now preserves an already plain one-sentence provider message, while turning generic connection exceptions into one plain AI-unreachable sentence.
- Updated only old expectations that described local gentle close asking first: `tests/test_router.py`, `tests/test_relay_round3.py`, and `tests/test_voice_loop.py`. AI plan approval and declined-plan tests are unchanged and continue to require confirmation/no action on decline.
- Restored the two original approval coverage cases alongside the new gentle-close cases: `tests/test_router.py` now verifies force-close cancel/yes behavior, and `tests/test_voice_loop.py` verifies the voice retry after silence using force close.
- Preserved all existing uncommitted Round 1 changes. No configuration secrets were read into this report.

## Verification

- **Test count:** `1,453 passed, 3 warnings` in 36.88s using the supplied Python 3.14 interpreter and `-p no:cacheprovider --basetemp=.pytest-run`. Pytest's default temp location was denied by the sandbox, so the run used a temporary directory inside the repository; the generated `.pytest-tmp/` and `.pytest-run/` folders were removed afterwards. The warnings are upstream deprecations from AnyIO, SpeechRecognition, and Google GenAI.
- `node --check ui/web/app.js`: passed.
- No live KOVA actions were attempted. The test fixtures keep app/window/UI operations fake. No application was opened or closed during test verification.
- Performance measurements are unrun. Startup, local first-answer latency, idle CPU/memory with Reaper off/on, and listening CPU are marked not measured in `docs/PERFORMANCE.md`.
- The docs search found “Definition of Done” only as a Day 7 heading in `docs/KOVA_7_DAY_PLAN.md`; it does not contain a separate checklist. I checked the stated Day 7 hardening items against the requested scope: offline/no-key coverage has tests added but unexecuted; performance procedure and README/launcher are present; release/tag action is intentionally omitted as instructed. The unexecuted tests and unmeasured performance remain unverified.

## Not verified

The Python suite is verified. Live front-window clicking, real voice approval timeout, real pywebview title refresh, and all performance figures remain unverified until run on the desktop; the performance script was not run.
