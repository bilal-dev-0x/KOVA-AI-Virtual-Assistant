# Day 3 (Thu 5 Nov): Memory + a Max that keeps watch 🧩

**Goal:** Max remembers you between restarts, warns you before your PC gets into trouble, and runs whole routines from one phrase.
**Roadmap phases:** 4 (Memory) and 2 (System Intelligence).
**End of day:** tested, committed and pushed after your OK. If time runs out, Stretch goes first, then Advanced. Core always ships.

---

## Where we start

| Already there (Day 1–2) | New today |
|---|---|
| CPU, RAM, disk, battery, GPU, top processes, `system_status` | Uptime, the health report, startup apps, power plans |
| Reminders queue that speaks between two listens | Alerts reuse the same queue |
| Tool registry + one approval per plan | Routines and memory tools use it as is |
| `logs/actions.jsonl` (every tool call) | Activity recall reads it |
| Nothing is remembered after a restart | `memory_engine.py` (SQLite) |

---

## Build order (one module at a time, each tested before the next)

### 1. `modules/memory_engine.py`: the memory store — Core
**Logic**
- One SQLite file: `data/memory.db` (gitignored, like `config.json`). It stays on this PC.
- Table `facts`: `id`, `text`, `kind` (fact or preference), `created`, `updated`, `uses`.
- Saving the same fact twice updates it instead of adding a copy ("my project folder is D:\Code" replaces the old folder).
- **Secrets guard:** text that looks like a password, PIN, OTP, API key, card or account number is refused with "I don't store passwords or keys." Checked before anything is written.
- Lazy: the database opens on first use, so startup stays fast.

**Tests:** save, update, list, forget, the secrets guard, and a fresh restart still finding the facts.

### 2. Remember, review and forget — Core
| You say | What happens | Safety |
|---|---|---|
| "remember I like lo-fi when coding" | Saved; Max repeats what it saved | Safe |
| "what do you remember about me?" / "what do you know about me?" | Reads the facts back (the first 5, then "and N more") | Safe |
| "what's my project folder?" | Finds the matching fact | Safe |
| "forget that" / "forget my project folder" | Deletes it after one confirmation | Controlled |
| "forget everything" | Deletes all facts | Critical (asks again) |

Local commands for the exact phrases (instant, offline). Tools `remember`, `recall`, `list_memories` and `forget` for the agent.

### 3. Recall by meaning — Core, with an offline fallback
**Logic**
- **Always:** local matching (shared words + the fuzzy matcher already used for songs and apps). Works offline and with the relay brain.
- **When Gemini is the brain:** each fact also gets an embedding (a list of numbers that captures meaning). Recall then finds "project folder" even if you saved "I keep my code in D:\Code".
- Embeddings are made once per fact and stored in the database, so a search costs one embedding call, not one per fact.
- **Quota check first:** I'll measure how many embedding calls the free key allows before relying on them. If it's tight, local matching stays the default.

### 4. Memories in agent requests — Core
- Before a request goes to the brain, the 3 most relevant facts (above a match score) are added as a short note: "Known about the user: ...".
- Nothing is added when nothing matches, so the prompt stays small.
- **Found in the voice test:** the "commands you ran without me" note sends local replies to the brain, and that includes what "what do you know about me" read out. Memory replies must be left out of that note (or shortened to "listed your memories"), so facts only reach the brain through the matched note above.
- The system prompt tells the brain to use the `remember` tool when you state a lasting preference. That tool is **Controlled**, so Max asks "Should I remember that you like lo-fi when coding?" before saving. That is the Advanced *auto-memory* item, nearly for free.

### 5. System intelligence — Core
- **Health report:** "how's my PC?" → CPU, RAM, disk, battery, **uptime**, and the 3 heaviest apps, in two spoken sentences plus a printed table. Builds on the existing `system_status`.
- **Startup apps (view only):** reads the Run registry keys and the Startup folder. "What starts with Windows?" Nothing is changed today.
- **Power plans:** "switch to high performance / balanced / power saver". Uses fixed `powercfg` commands only, never text from the AI. Controlled, and **undo** switches back to the previous plan.

### 6. Alerts — Advanced
**Logic**
- One background thread, checking every 60 s (one cheap psutil read, near-zero CPU). **Off by default.**
- "turn on alerts" / "turn off alerts", saved in `config.json`.
- Thresholds in `config.json`: battery below 20% (on battery only), RAM above 90% (names the app using the most), disk below 10% free.
- An alert is spoken between two listens, like reminders, so it never talks over you.
- **Cooldown:** the same alert repeats at most every 30 minutes.

### 7. Routines — Advanced
**Logic**
- `routines.json` (a `routines.json.example` is committed): a name, trigger phrases, and steps. Each step is a tool call, for example `{"tool": "open_app", "args": {"name": "vs code"}}`.
- Checked at load: unknown tools or bad arguments are reported, never guessed.
- "coding mode", "start my morning routine", "what routines do I have?"
- **One approval** for the whole routine (like agent plans). Critical steps still ask on their own.
- Steps run in order; on the first failure Max **stops** and says which step failed and what already ran.

Example routines: *coding mode* (VS Code, terminal, lo-fi music, high performance plan) and *morning routine* (health report, time, today's reminders).

### 8. Activity recall — Advanced (if time allows)
- "what did I do yesterday?", "which apps did I open most this week?"
- Reads `logs/actions.jsonl`. No new tracking, no polling.

### Stretch
- Max **suggests** a routine after seeing the same steps 3 times ("You often open VS Code then lo-fi. Make it a routine?").
- Carried from Day 2: **learned phrases** (cache a phrase the brain mapped to a tool) and **spoken progress** in long plans.

---

## Safety rules for today
- Facts stay in `data/memory.db` on this PC; the database and `routines.json` are gitignored.
- No passwords, PINs, keys or card numbers are ever stored.
- Forgetting asks first; forgetting everything asks twice.
- `powercfg` runs as a fixed command with a fixed plan ID; the AI never writes a command.
- Alerts are opt-in.

## Your test (about 30–40 min, voice or text)
1. "remember I like lo-fi when coding" → "remember my project folder is D drive coding arc"
2. Restart Max → "what do you know about me?" → "where is my project folder?"
3. "forget my project folder" → confirm → "what do you know about me?"
4. "remember my password is 1234" → Max refuses.
5. "how's my PC?" → "what starts with Windows?" → "switch to high performance" → "undo"
6. "turn on alerts" → open many heavy apps until RAM is high (or wait for a low battery).
7. "coding mode" → approve → watch each step; then close VS Code first and try a routine that needs it.

**Demo:** "remember I like lo-fi when coding" → restart Max → "what do you know about me?" → "coding mode".

## Checklist
- [x] 1. Memory store + secrets guard
- [x] 2. Remember / review / forget
- [x] 3. Recall by meaning: offline only (stems, a synonym table, near spellings). Embeddings were left out:
  the free key allows about 20 requests a day and the relay brain has none, so local matching is the default.
- [x] 4. Memories in agent requests + auto-memory (`MEMORY_IN_AI_REQUESTS`, on by default; memory replies
  no longer go into the "commands you ran" note)
- [x] 5. Health report, uptime, startup apps (read only), power plans with undo. This PC has only the Balanced
  plan, so the example "coding mode" has no power-plan step. To bring High performance back, run
  `powercfg -duplicatescheme 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c` yourself, then add the step to routines.json.
- [x] 6. Alerts (off by default; "turn on alerts")
- [x] 7. Routines (`routines.json.example`; copy it to routines.json to make your own)
- [x] 8. Activity recall
- [x] Voice test of steps 1–2 → fixes → commit → push (5 Nov evening)
  - Pause / stop "it", "opposite" and "positive" heard for "pause it", "forgot" for "forget"
  - "how is my CPU and RAM" stays local despite the "and"
  - Close a window or browser tab by what it shows ("close lofi music", "close the song"), with a short spoken name
  - Approval questions name the window; new `close_tab` tool (Controlled)
  - "forget that" asks which one when it's unclear; "forget X and Y" forgets both with one undo
  - Repeating the action ("forget that") is a yes; "ok bye" / "never mind" drops a question
  - Calculator no longer reported "still open" (cloaked window)
  - Natural voice gives up in 1.5 s when DNS fails, backing off 2 → 30 min (the real fix was the Wi-Fi DNS)
- [x] Voice test of steps 3–8 → fixes → commit → push (6 Nov, 01:28–01:50)
  - Recall questions answered locally: "where do I keep my code", "what music do I like", "when was I born"
  - A related word alone is no longer a match ("where do I keep my food" didn't mean the project folder)
  - "open my project folder" / "open D drive coding arc" opens the folder (new `open_folder` tool)
  - "close terminal" closes terminal windows one by one, never the one Max runs in (`close_terminals`)
  - "so slow" isn't a joiner; looser uptime and "which apps I did open most" phrasing
  - Misheard: "IP performance", "are alert song", "RMS on", "proteins", "and do them"; "undo the alerts"
  - "remember my" with nothing after it is refused; "forget memory 15" forgets by number
  - Break time no longer toggles the music (the media key can't tell pause from play)
- [x] Text test of 66 hard commands, with real memory, config and logs kept safe (6 Nov)
  - "my wifi pin is 4455" is now refused; "+92 300 1234567" is a phone number, not a card (committed `d2365b8`)
  - "this week" is a period, not "this" = the last app (committed `d2365b8`)
  - "remember X and open them" goes to the agent; without a brain only the fact is saved
  - "start my evening routine" says there is no such routine and lists yours; "last week", not "in last week"
