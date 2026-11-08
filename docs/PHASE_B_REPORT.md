# KOVA Phase B report

**Date:** 8 November 2026  
**Status:** Complete  
**Scope:** Assistant conversation history and truthful request states. No commit or push was made.

## What changed

- Added a local JSONL conversation history at `data/history.jsonl`, capped at the latest 500 messages.
- History rows have stable IDs and timestamps. Credential-like messages are skipped using KOVA's existing secret detector; corrupt rows are ignored, and writes replace the file atomically.
- Assistant reloads the saved conversation on startup. Live messages wait until history is restored, preventing a new turn from appearing above older messages.
- The status now distinguishes Thinking, Working, Waiting for approval, and Request failed. A new request clears the previous error state.
- Approval round-trips always return the controller to Thinking when the dialog is answered, dismissed, or the UI bridge closes.
- Added isolated tests for persistence, secret filtering, history limit, API reads, error state, approval state, and updated chat event IDs.

## Verification

- Focused Phase B and controller checks: **33 passed**, 1 existing pytest deprecation warning.
- Full test suite: **1,355 passed**, 3 existing dependency deprecation warnings.
- `node --check ui/web/app.js`: passed.

## Limits

- The stored history is local to this PC and is not encrypted. Credential-like text is filtered, but this is pattern detection rather than a guarantee that every sensitive message will be recognized.
- The web page renders the latest 200 chat bubbles while the local file retains up to 500 messages.
- A live PyWebView/voice session and screenshot review could not be completed: the desktop automation runtime (`@oai/sky`) timed out during initialization and reset its kernel. KOVA was not already running, so no visual result is claimed. Automated tests and the JS syntax check passed.
