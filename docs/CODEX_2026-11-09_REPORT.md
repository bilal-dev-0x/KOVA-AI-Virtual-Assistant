# KOVA Round 1 Work Report — 9 November 2026

## Scope completed

Implemented the approved Round 1 handoff items A1, A2, A3, A4, and A7. Changes remain uncommitted and unpushed, as the handoff requires. Round 2 has not started.

- **A1 — Follow-up answers:** A short answer or a selected choice can resume a pending clarification for a UI action. The pending question expires after 60 seconds. A clearly new command cancels it. The original user turn is retained for intent checks.
- **A2 — Internet speed:** Added the local `internet_speed` command and SAFE tool. It opens Fast.com, waits briefly, and reads a displayed rate only when the UI exposes one. If the number cannot be read, Max says so instead of inventing a result.
- **A3 — Intent checks:** Destructive or broad tool plans require the user’s words to show the requested action. Negated requests do not pass. Organise, delete, force-close, multi-tab, and terminal tools now have action-specific intent words.
- **A4 — Near-name window targeting:** Fuzzy matching is limited to visible windows and requires a unique sufficiently close match. Ambiguous matches return choices. Successful fuzzy matches say which app was actually targeted.
- **A7 — Voice fragments:** Unmatched one-word fragments and the exact noise phrases `there is` / `theres` are ignored by the awake voice loop and logged as ignored; real one-word commands remain usable.

Added focused tests for each item and adjusted the existing delete confirmation test to use explicit delete wording.

## Verification

- Full Python test suite: **1442 passed**, with **3 dependency warnings**, in 36.63 seconds.
- JavaScript syntax check: `node --check ui/web/app.js` passed.
- `git diff --check`: passed. Git emitted only a line-ending notice for `tests/test_day4_files.py` (LF will be converted to CRLF on a later write); the file was not reformatted.
- Live UI check: “what’s my internet speed” routed locally and opened Fast.com. KOVA’s UI automation could read the page labels but did not expose a numeric Mbps/Kbps/Gbps result, so Max correctly returned the honest fallback asking the user to check the page. No speed value was fabricated.

## Live-test cleanup note

The temporary KOVA dashboard/debug session was stopped and port 9333 is now closed. The Fast.com Chrome window remained visible after an attempted close through its exact window title, so verify and close that test tab manually if it is still open. No other Chrome tabs were targeted.

## Handoff

Round 1 is ready for Bilal’s live review. Review the changes and test them in the running KOVA app before starting the next round. No commit, push, tag, or history rewrite was performed.
