# Day 4 (Fri 6 Nov): Eyes and hands 👁️✋

**Goal:** Max can look at your screen when you ask, move and arrange your windows, and work with your files safely.
**Roadmap phases:** 5 (Screen understanding) and 8 (Desktop control).
**End of day:** tested, committed and pushed after your OK. If time runs out, Stretch goes first, then Advanced. Core always ships.

---

## Where we start

| Already there (Day 1–3) | New today |
|---|---|
| `app_windows()`, window titles, bring to front, close one window or tab | Minimise, maximise, restore, snap, arrange two apps side by side |
| `search_files` opens Windows search in Explorer; `open_folder` opens a folder | Find files and read the results, open, create, rename, organise Downloads, delete to the Recycle Bin |
| One approval per plan, critical steps ask again, undo (10 deep) | File and window tools use them as they are |
| Brain providers: Gemini (text) and relay | Providers can also **look** at one picture: Gemini with an image, relay with a file Claude reads |
| Pillow 12.3, comtypes 1.4 and pywin32 312 are installed | UI Automation through comtypes (no new big library) |

---

## Build order (one module at a time, each tested before the next)

### 1. `modules/windows.py`: window control — Core
**Logic**
- Uses Windows' own calls through `ctypes`, like `system_control` already does. Nothing new to install.
- "list my windows" reads every visible window as *app: title*, at most 10, and leaves out cloaked and tool windows.
- "minimise chrome", "maximise vs code", "restore notepad", "focus discord" / "switch to discord".
- "snap chrome left", "put vs code on the right". Each window goes to half of the screen it is on, so it works with two monitors too.
- "put VS Code on the left and Chrome on the right" stays **local**. It is one arrange command even though it has an "and", like "how is my CPU and RAM".
- "show desktop" / "minimise everything" (Win+D).
- **Undo:** every move first saves where the window was (`GetWindowPlacement`), and undo puts it back.
- With several windows of one app, Max moves the one in front; part of a title picks another
  ("minimise the gmail chrome window" through the agent, `window` argument). An app that isn't in apps.json is
  found by its program name or by words in its title ("maximise lofi music").
- "it" means the last app: "open notepad" → "put it on the right".
- Safety: all of these are Safe, because they change nothing that can't be put back.
- **DPI:** Max calls `SetProcessDpiAwareness` (per-monitor v2) at startup. Without it, snap and arrange land in
  the wrong place on a display scaled to 125% or 150%.
- **Restore first:** a minimised or maximised window is restored (`SW_RESTORE`) before it is snapped, or
  `SetWindowPos` doesn't take.
- **Admin windows:** a normal process can't move a window running as admin (Task Manager, installers; Windows
  blocks it, "UIPI"). Max detects this and says "I can't move that window, it's running as admin" instead of
  failing silently.

**Tests:** placement maths for one and two monitors, arrange phrases (local, not the agent), undo restores the old placement, unknown app, app with no window, restore before snapping, the admin-window message.

### 2. `modules/screen.py`: look at the screen — Core
**Logic**
- **A screenshot only on command**, never in the background. Pillow's `ImageGrab` takes it.
- It is taken of the **active window** by default ("what's this error"), or the whole screen when you say so ("what's on my screen").
- It is shrunk to at most 1280 px wide and saved as JPEG **in memory**. A normal screenshot is never written to disk.
- `Provider.look(image, question)` is a new method beside `search()`:
  - **Gemini:** the picture and the question go in one request (`Part.from_bytes`), so one look costs one request of the daily quota.
  - **Relay:** the picture is written to `logs/relay/screen.jpg` for Claude Code to read, then deleted when the answer comes.
  - **No brain:** "Looking at the screen needs Gemini."
- Phrases: "what's this error", "what's on my screen", "summarise this page", "read this for me", "explain this code".
- Max prints `📸 Looking at <window title>` first, so you always know what was sent.
- The `look_at_screen` tool is **Controlled** for the agent: if Gemini decides to look on its own, Max asks first. Your own local phrase counts as the yes.
- **Privacy blocklist** (`SCREEN_BLOCKLIST` in `config.json`): when the active window's title matches a password
  manager, a bank, WhatsApp or Telegram, or "InPrivate" / "Incognito", Max refuses the screenshot. Say
  "look anyway" to allow it once.
- **Follow-ups:** the last screenshot stays in memory for about 60 seconds, so "and how do I fix it?" asks about
  the same picture instead of taking a new one. It still costs one Gemini request, but it is the picture you
  already saw named. After 60 seconds it is dropped.
- **Quota check first:** one real test call to confirm the free key accepts images and to see what one look costs.

**Tests:** image is shrunk and stays in memory, relay writes then deletes the file, the no-brain message, the agent's look asks first, the phrases route locally, the blocklist and "look anyway", a follow-up reuses the picture and an old one expires.

### 3. `modules/files.py`: file tools — Core
**Logic**
- **Allowed places:** your user folders (Desktop, Documents, Downloads, Pictures, Music, Videos) and non-system drives (D:). **Never:** `C:\Windows`, Program Files, ProgramData, AppData, drive roots, or the KOVA folder itself. This is checked on the real full path after `..` and shortcuts are resolved.
- The user folders come from Windows (`SHGetKnownFolderPath`), not hard-coded paths, because Desktop and
  Documents may be moved to OneDrive.
- **Find** (Safe): "find my CV", "find files called invoice". It searches the allowed places (and the project folder from memory) by name, stops after 3 seconds or 20 hits, and reads back the top 5 with their folders. The old `search_files` (Explorer search) stays for "search ... on my pc".
- **Open** (Safe): opens a document, picture or folder from the results ("open the second one"). **Programs and scripts** (`.exe .bat .cmd .ps1 .vbs .msi .lnk .reg`) are refused, because opening them would run code.
- **Create** a folder or an empty text file (Controlled). Undo removes it only if it is still empty.
- **Rename** (Controlled). It refuses if the new name already exists; undo renames it back.
- **Organise Downloads** (Controlled):
  1. A **preview** first: "34 files: 12 pictures, 9 documents, 6 installers, 4 archives, 3 other. Move them into folders?"
  2. One approval moves them into `Pictures`, `Documents`, `Installers`, `Archives`, `Videos`, `Music`, `Code` and `Other`.
  3. Skipped: files still downloading (`.crdownload`, `.part`, `.tmp`) and anything changed in the last 2 minutes.
  4. A name already in the target folder is never overwritten: the file becomes "name (1).ext" (then (2), ...).
  5. Every move is written to `data/organise_log/<time>.json` with the final name. **Undo** moves everything back, even after a restart ("undo organising Downloads").
- **Delete** (Critical): **to the Recycle Bin only**, through Windows' own undoable delete (`SHFileOperationW` with `FOF_ALLOWUNDO`). At most 20 files at once, inside the allowed places only. It asks again right before it runs.
  - **Fixed drives only:** USB sticks, memory cards and network drives have no Recycle Bin, so Windows would delete
    there *permanently* without a word. Max checks `GetDriveTypeW` and refuses anything that isn't a fixed drive.
  - `SHFileOperationW` needs the paths ending in two null characters; a test checks how the buffer is built.

**Tests:** the allowed-place check (including `..` and paths outside), programs refused, rename collision, organise preview and moves in a temp folder, a name clash becomes "name (1)", the undo log after a "restart", delete only within limits and only on fixed drives, the double-null path buffer (the Recycle Bin call itself is faked).

### 4. Windows UI Automation — Advanced
**Logic**
- Reads the real buttons, menus, fields and tabs of the **active window** through Windows' UI Automation (UIAutomationCore through comtypes; the `uiautomation` package is a fallback only if comtypes is awkward on Python 3.14).
- "what can I click here?" reads back up to 15 named controls.
- "click Save", "click the Sign in button" (Controlled). The name must match **exactly one** control; with several, Max reads them out and asks which.
- "type hello into the search box" (Controlled). It **refuses password fields** (`IsPassword`) and never types into Max's own console.
- **Check before acting:** right before a click, Max checks that the window title is still the one it read. If it changed, Max stops and says so.
- **Limits:** reading the controls stops after about 2 seconds and a fixed depth. Chrome and Electron apps
  (VS Code, Discord) have huge trees and would otherwise freeze Max. comtypes builds its UIAutomationCore
  wrapper once and reuses it from its cache.
- *As built, after review:*
  - **Risky buttons** (Delete, Send, Pay, Buy, Uninstall, Sign out, ...) are asked about again right before
    the click: "Click Send in WhatsApp? It can't be undone."
  - Typing never replaces text already in a field (it goes in at the cursor), never puts a new line in a
    one-line box (Enter would send it half written), stops at 2000 characters, and checks the cursor is
    still in the field right before.
  - Keys: only enter, escape, tab and ctrl + s/a/c/v/z/f/n/t ("press ctrl s").
  - An admin window or one that isn't responding is refused with a reason; UI Automation calls time out
    after 1–2 s, and a CacheRequest reads 5 properties per control in one go.
  - If Windows blocks the typing or a key press (`SendInput` takes fewer keys than sent), Max says so.

### 5. Multi-step desktop tasks — Advanced
- The agent loop already plans steps. What's new: each window, file or UI step is shown in the trace before it runs, controlled steps share one approval, and the **check before acting** from step 4 is applied on every step.
- **Stops safely:** a missing control, a changed window or a failed step ends the plan and Max says what already ran (like routines).
- Example: "open notepad, type shopping list, then save it to my desktop".
- *As built:*
  - A tool result can **halt** the plan (`Result.halt`). The rest of that batch isn't run, the conversation
    is reset, and Max says e.g. "The window changed to Save changes?, so I stopped. I already did: press
    ctrl+a, type 'shopping list' into the text field."
  - A dialog the app has open (Save As, "Do you want to save?") is the window in front, and a dialog
    popping up between reading and clicking halts the plan.
  - The brain is told to read with `ui_read` before clicking, to pass `window=` with the title it expects,
    and not to try another way after a stop.
  - Found while building: an approval answered with neither yes nor no (`None`) let a controlled step run.
    It now counts as no.

### Stretch — not done today
The plan drops Stretch first; today's diff is already large. Moved to Day 5's Stretch:
- Vision + UI Automation ("click the blue Sign in button" on apps without accessible names).
- From Day 3: Max suggests a routine. From Day 2: learned phrases and spoken progress.

---

## Safety rules for today
- A screenshot is taken only when you ask, of one window (by `PrintWindow`, so windows lying on top are
  not in it), or of the monitor you're working on. It is named in the terminal, kept in memory for about
  60 s and never saved (the relay's copy is deleted after the answer). Private windows (banks, password
  managers, WhatsApp, Telegram, InPrivate/Incognito; `SCREEN_BLOCKLIST` in config.json) need "look anyway".
- Files are touched only in your own folders and on D:. Never in Windows, Program Files, AppData, Max's
  own folder, the Recycle Bin or SteamLibrary, nor inside an installed program or game. Programs and
  scripts are never opened, and a file is never renamed into one.
- Delete means the Recycle Bin, is Critical (the question shows the full paths), handles at most 20 items
  and folders of at most 200 files, and works only on fixed drives.
- Organising is only for Downloads, Desktop or Documents, never overwrites, and can be undone even after
  a restart.
- UI Automation works only in the window in front. Risky buttons are asked about again. Password fields
  and admin windows are refused.
- **Your standing rule:** don't hurt either PC's performance. No background screenshots, no polling, light
  tests. Every search and read has a time limit.
- The AI still writes no shell commands; every action is a tool with a safety level.

## Your test — steps 1–5 in one go (about 60 min, text first, then voice)
Run `python main.py --text` (later the same in voice). Use test files, not your real work.

**1. Gemini and the screen (first, because it uses the real Gemini key)**
1. Set `"BRAIN_PROVIDER": "gemini"` in config.json for this part. Open a web page with an error (or any
   article) → "what's this error?" Check that the terminal says `📸 Looking at …` with the right window, and
   that the answer is about that page. This is the **quota check**: one look = one request. Note the model
   it used and whether the free key accepted the picture.
2. "and how do I fix it?" → an answer, with no new `📸` line (the same picture).
3. Open your bank's site, or a WhatsApp Web tab, in front → "what's on my screen" → refused as private →
   "look anyway" → it looks.
4. "what's on both screens" (only if you have two monitors).

**2. Windows**
5. "list my windows"
6. Open Notepad and Chrome → "put notepad on the left and chrome on the right" → check there's no gap, and
   **that nothing hides behind the taskbar** (Max read your screen as the full 1920×1080; that's right only
   if your taskbar auto-hides) → "undo"
7. "maximise chrome" → "snap chrome right" → "minimise it" → "switch to chrome"
8. Snap Chrome yourself with Win+Left → "put chrome on the right" → "undo" → back on the left
9. Task Manager open → "snap task manager left" → "…running as admin"
10. "show desktop" → "undo"

**3. Files** (with a few test files)
11. "find <a file you know>" → "open the first one"
12. "create a folder called KOVA test on my desktop" → "rename it to KOVA final" → "delete it" → the
    question shows the full path → yes → it's in the Recycle Bin
13. Put 5–6 test files (a picture, a pdf, a zip) in Downloads → "organise my downloads" → check the preview →
    yes → restart Max → "undo organising my downloads" → all back
14. "organise d drive" → refused. "delete D:\SteamLibrary" → refused.

**4. UI Automation**
15. Notepad in front → "what can I click here?"
16. "type hello into the text area" → "type more" (added, not replaced) → "press ctrl a"
17. "click File" → the File menu opens

**5. Multi-step**
18. With Gemini as the brain: "open notepad, type shopping list, then save it to my desktop".
    ⚠️ **The Save As step is the one most likely to need a fix.** In Windows' own Save dialog Max *replaces*
    the File name box (it starts out as "*.txt" or "Untitled", which Windows put there). Check: the box shows
    only `...\Desktop\shopping list.txt`, the file appears on your Desktop, and Max clicked Save itself.
    If the box shows "Untitled" joined to the name, or the dialog stays open, tell me what it showed.
19. While a plan runs, click another window into the front → Max should stop and say what it already did.

**Demo:** "what's this error on my screen?" → "put VS Code on the left and Chrome on the right" → "organise my
Downloads folder".

## Checklist
- [x] 1. Window control + undo (`modules/windows.py`; tools `open_windows`, `move_window`, `side_by_side`,
  `show_desktop`). DPI aware; restore before snapping; admin windows named; undo puts every window back, also
  after Windows' own snap. "open X on the left and Y on the right" goes to the agent; "bring me water" is not
  a window.
- [x] 2. Look at the screen (`modules/screen.py`, `Provider.look` for Gemini and relay, tool `look_at_screen`).
  `PrintWindow` with a privacy-checked fallback; blocklist + "look anyway"; 60 s follow-ups; the monitor in
  front or all screens; no blank pictures sent. **The live quota check is the first item of your test.**
- [x] 3. File tools (`modules/files.py`; tools `find_files`, `open_file`, `create_item`, `rename_item`,
  `organise_folder`, `undo_organise`, `delete_items`). Real folders from Windows (Desktop and Documents are in
  OneDrive on this PC).
- [x] 4. UI Automation (`modules/ui_automation.py`; tools `ui_read`, `ui_click`, `ui_type`, `ui_press_keys`).
  comtypes on Python 3.14 works; `CUIAutomation8` with 1–2 s timeouts; a CacheRequest for reading.
- [x] 5. Multi-step desktop tasks with checks before acting (`Result.halt`, dialogs in front).
- [ ] Stretch: not done (see above)
- [x] Each step reviewed by a second Claude session; all its findings fixed and tested. 929 tests.
  Read-only checks on this PC only: nothing moved, opened, clicked, typed, deleted or sent.
- [x] Live test, text and voice (6 Nov, 11:08–13:57) → fixes:
  - UI typing went into the terminal behind Max's (Claude Code's): terminals are now never typed, clicked or keyed in
  - "organise d drive" went to the brain, which sorted Documents: other folders are refused locally
  - `D:\` never matched (`[\/]` was only a slash); "open my X folder now"; folders saved as "X in Y folder in d drive"
  - Spoken names: "pic dot png", "pitbull png", near spellings ("vichora" → Visora), letters spelled out
  - "click the chat Rehan" → Rehan; "open Mr Ahmed chat" goes to the brain; close any window by title
  - Two tabs with one title: counted to tell whether Ctrl+W worked; backspace key; Steam store search
  - A long Roman Urdu question with "gpu" in it goes to the brain, not the GPU report
  - One-time codes in window titles are hidden in kova.log
  - Voice listens in en-US and en-IN at once and switches to the one that heard you best
  - Worked: the screenshot quota check (~2 s), the approvals, Roman Urdu answers, game advice from the real RAM/GPU
  - Still to test once Gemini's daily quota is back: the Save As flow, multi-step plans
- [x] Commit (Bilal: one commit for everything); push after his OK
- [x] Fewer questions (Bilal: "har cheez pe na pooche"): typing, keys, ordinary clicks, create and rename no
  longer ask (undo or ctrl+z takes them back); in one plan, a kind of step said yes to once isn't asked again.
  Still asked: delete, closing apps, risky buttons (Delete/Send/Pay...), the AI's own screenshot, organising.
