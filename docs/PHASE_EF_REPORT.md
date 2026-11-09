# KOVA Phases E and F report

Date: 9 November 2026.

## Phase E: Settings and first start
- **Start with Windows** (`modules/startup.py`): a switch in Settings. On writes one shortcut, `KOVA.lnk`, in the user's own Startup folder (windowless `pythonw main.py --ui`); off removes only that shortcut. No admin rights, no registry. Needs `pywin32` (added to `requirements.txt`). Tested with a fake folder; **not switched on for real** on Bilal's PC, because writing to the Startup folder was to be his decision.
- **Brain in Settings**: which brain answers now, and whether a Gemini key is saved (yes or no, never the key). A new key can be pasted into a password box; it is checked, written to `config.json`, and the brain switches over at once (fresh session). The page never receives the key back.
- **First start**: when there is no Gemini key and setup was never finished, a welcome sheet asks for the persona (Max or Mira) and, optionally, a Gemini key. "Skip for now" is fine: local commands work without any account. It is shown once (`SETUP_DONE`).

## Phase F: screenshots, top problems, performance
All eight screens were captured from the running app (WebView2 remote debugging) at 1180x720, plus the desktop Reaper above a maximised dashboard.

Five problems fixed after the review:
1. Activity showed raw tool ids and codes ("cpu_usage · local", "Succeeded · not needed"). Now "CPU usage", "GPU usage (AI)", "Done, you approved it, can be undone (...)".
2. Files showed raw ISO times ("2026-10-16T09:15"). Now "340 B, changed 16 Oct 09:15" / "changed yesterday 19:38".
3. Misheard voice fragments filled the saved conversation ("camera", "IMAX", each followed by "Sorry, I didn't catch that"). They still show live, but are no longer saved.
4. Middle-dot meta strings in System, the clock and the rail were replaced by plain punctuation.
5. The folder picker in Files used a light native look; it now follows the dark theme.

Performance, measured on the running app, idle on Home with the desktop Reaper on (10 s):
- CPU: 0.3 % of the whole PC.
- Memory: about 290 MB private memory for KOVA in total (Python about 96 MB, WebView2 browser, GPU and renderer processes the rest; the summed working sets look larger, 715 MB, because WebView2 processes share pages).

## Not done / limits
- Start with Windows was not turned on for real (see above).
- The welcome sheet was checked in a browser with a stubbed API, not by deleting the real key.
- Gemini's free quota and the slow server fallback are unchanged.
