# KOVA Phase C report

**Date:** 8 November 2026  
**Status:** Implementation complete; native visual review deferred to Phase F  
**Scope:** Applications, Files, System and Activity screens. No commit or push was made.

## What changed

- **Applications:** lists Start-menu apps, searches by name, shows a best-effort running status, and queues Launch/Open through the Agent.
- **Files:** browses allowed user folders, shows file size and modified time, and searches with a 3-second/100-result bound. Hidden/system items, symlinks, blocked folders and protected directories are skipped or refused. Open, Show and Delete use existing Agent commands; Delete still reaches the normal approval prompt.
- **System:** shows CPU, memory, disk, battery, GPU and the eight highest-RAM processes. It refreshes every two seconds only while the System page is active and the dashboard is visible. CPU/RAM/GPU reuse the dashboard's existing sample to avoid duplicate polling.
- **Activity:** shows the newest 100 local action-log rows with result and approval status, without sending raw tool arguments to the page. Reversible actions are labeled; an Undo button appears only when the latest undo is still in the current Agent session.
- Added data/API, action-routing and screen checks. The old test that required these pages to say “Not built yet” now checks that the real controls are present.
- Also completed two reviewed Phase B fixes: history waits for `pywebviewready` without a premature timeout fallback, and the error status clears after six seconds.
- Applied polish from Bilal's live review: dark scrollbars, relative Activity timestamps (“Today” / “Yesterday”), and tighter Files/Activity spacing.

## Verification

- Full suite after live-review polish: **1,367 passed**, 3 existing dependency deprecation warnings.
- After the visual-polish edits: **19 focused UI tests passed**, 1 existing pytest deprecation warning.
- `node --check ui/web/app.js`: passed.
- `git diff --check`: passed.

## Limits

- Bilal live-tested the core screens and reported the listed polish issues. Codex's own screenshot capture remains unavailable because the desktop automation runtime timed out initializing earlier in this session; Phase F should still include the planned screenshot review.
- App running status is best-effort process-name matching; unusual Store apps may be shown as not running even when open.
- Undo is session-only: after KOVA restarts, previous action records remain visible, but their Undo buttons do not.
- A pre-existing behavior noted during review remains outside Phase C: Max can ask to close a window before confirming that the requested window exists.
