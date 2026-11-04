<p align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&color=0:020617,45:0EA5E9,100:8B5CF6&height=175&section=header&text=KOVA%20AI%20Virtual%20Assistant&fontSize=38&fontColor=ffffff&animation=fadeIn&fontAlignY=36&desc=Voice%20commands%20%7C%20Gemini%20AI%20replies%20%7C%20News%20and%20app%20automation&descSize=14&descAlignY=56" alt="KOVA AI Virtual Assistant banner" />
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.10+" />
  <img src="https://img.shields.io/badge/Type-Voice%20Assistant-0EA5E9?style=for-the-badge" alt="Voice Assistant" />
  <img src="https://img.shields.io/badge/Gemini-API%20Powered-8B5CF6?style=for-the-badge&logo=googlegemini&logoColor=white" alt="Gemini API" />
  <img src="https://img.shields.io/badge/License-MIT-22C55E?style=for-the-badge" alt="MIT License" />
</p>

---

## Overview

KOVA AI Virtual Assistant is a Python-based voice assistant that listens for a
wake word, records microphone input with `sounddevice`, converts speech to text,
responds out loud, opens common websites and apps, plays saved music links, reads
date/time, fetches news headlines, controls volume and playback, sets reminders,
and hands anything more complicated to Gemini as a multi-step plan.

Routing is hybrid: a simple command (`open chrome`, `what time is it`) runs
locally — instantly, offline and free — while a request with several parts
(`close whatever uses the most ram except vs code`) goes to the agent, which
plans tool calls and runs them only after the safety checks in `permissions.json`
pass.

The latest version is configured around Gemini, supports a configurable Gemini
model through `config.json`, and addresses the user naturally as `Commander`.

---

## Feature Highlights

| Feature | Current Behavior |
|---|---|
| Wake word mode | Starts command mode when it hears `max` (configurable `WAKE_WORDS`) |
| Microphone input | Records until you stop talking, with noise auto-calibration |
| Speech recognition | Uses `speech_recognition` with Google recognition |
| Spoken replies | `gTTS` online, Windows `pyttsx3` voice as an offline fallback |
| Command router | A table of regex commands; simple ones run locally and free |
| AI agent | Multi-part requests go to Gemini with tool calling and a step limit |
| Safety levels | `safe` / `controlled` / `critical`, tuned by `permissions.json` |
| Undo | Reverses the last reversible action (`undo`, 10 deep) |
| Personalized tone | Addresses the user as `Commander` via `USER_TITLE` |
| App automation | Opens, closes, restarts and force-closes apps from `apps.json` or the Start menu |
| Web shortcuts | Opens YouTube, Google, Spotify, GitHub and more in the browser |
| Music library | Plays saved links from `music_library.py`, else searches YouTube |
| News headlines | Live headlines via Gemini search grounding, else `NEWS_API_KEY` |
| Reminders | Voice timers and clock-time reminders, spoken when they fire |
| Date and time | Speaks the current date and current time |
| Plugins | Drop a `@tool` file into `plugins/` and the AI can call it |

---

## Project Structure

```text
Jarvis-AI-Virtual-Assistant/
|-- main.py                 # entry point: text mode, voice mode, mic test
|-- desktop_agent.py        # Agent: wake words, command router, hybrid routing
|-- brain/                  # AI providers behind one interface
|   |-- base.py             #    Provider / Session / Reply / ToolCall
|   |-- gemini.py           #    Google Gemini, model fallback + daily budget
|   |-- relay.py            #    file-based relay used for local testing
|   `-- budget.py           #    per-day request counting
|-- modules/
|   |-- agent_loop.py       # the AI plan/act/read loop (max 6 steps)
|   |-- tool_registry.py    # every ability is a @tool with a safety level
|   |-- tools.py            # built-in tools (apps, system, media, reminders)
|   |-- actions.py          # runs tools, logs them, remembers what to undo
|   |-- permission_manager.py  # permissions.json: always_allow / blocked
|   |-- system_control.py   # open/close/force-close, apps.json + Start menu
|   |-- performance_monitor.py # CPU, RAM, disk, battery, top processes
|   |-- voice_handler.py    # recording, noise gate, gTTS + offline fallback
|   |-- reminders.py        # timer threads
|   |-- fuzzy.py            # speech-recognition typo tolerance
|   `-- logger.py           # logs/kova.log
|-- plugins/                # any .py here self-registers as a tool
|-- tests/                  # pytest suite (no microphone, no network)
|-- docs/                   # build plans
|-- music_library.py
|-- apps.json               # apps and websites KOVA can open/close
|-- permissions.json.example
|-- config.json.example
|-- requirements.txt
|-- requirements-dev.txt
|-- pytest.ini
`-- README.md
```

---

## Tech Stack

| Tool | Purpose |
|---|---|
| Python | Main programming language |
| google-genai | Gemini AI responses, tool calling and search grounding |
| SpeechRecognition | Speech-to-text recognition |
| sounddevice + numpy | Microphone recording, noise gate and hum removal |
| gTTS / pyttsx3 | Text-to-speech: online first, Windows voice as fallback |
| psutil | CPU, RAM, disk, battery and process inspection |
| requests | News API requests (fallback) |
| webbrowser / subprocess | Website and app automation |

---

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

The suite in `tests/` covers the command router, the agent loop and its
approval rules, permissions, tool schemas, reminders, the Gemini and relay
providers, system control, performance reporting and the voice pipeline.
Tests never touch the microphone, the network or your real `logs/` —
`tests/conftest.py` isolates all of it.

`mic_smoke.py` is a separate **manual** script that really does use the
microphone; run it by hand with `python mic_smoke.py`.

---

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/bilal-dev-0x/Jarvis-AI-Virtual-Assistant.git
cd Jarvis-AI-Virtual-Assistant
```

### 2. Create and activate a virtual environment

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

macOS/Linux:

```bash
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Create your local config

Windows:

```powershell
copy config.json.example config.json
```

macOS/Linux:

```bash
cp config.json.example config.json
```

Then edit `config.json`:

```json
{
  "GEMINI_API_KEY": "PASTE_YOUR_GEMINI_API_KEY_HERE",
  "GEMINI_MODEL": "gemini-3.6-flash",
  "BRAIN_PROVIDER": "gemini",
  "NEWS_API_KEY": "",
  "NEWS_COUNTRY": "pk",
  "ASSISTANT_NAME": "Max",
  "USER_TITLE": "Commander",
  "WAKE_WORDS": ["max", "imax", "macs", "maks", "mac"],
  "AWAKE_TIMEOUT": 60
}
```

`GEMINI_API_KEY` is required for AI replies. `NEWS_API_KEY` is optional and only
used as a fallback when Gemini search grounding is unavailable.

Copy `permissions.json.example` to `permissions.json` if you want to loosen or
tighten per-tool safety: `always_allow` skips a confirmation, `blocked` refuses
the tool entirely — for local voice commands and AI requests alike.

---

## Run

```bash
python main.py
```

First say the wake word:

```text
max
```

Then try commands like:

| Command | Action |
|---|---|
| `hello` | Greets the Commander |
| `open youtube` | Opens YouTube |
| `open google` | Opens Google |
| `open spotify` | Opens Spotify |
| `play never gonna give you up` | Opens a saved music link |
| `global news` | Reads up to five top headlines |
| `today date` | Speaks the current date |
| `current time` | Speaks the current time |
| `open whatsapp` | Opens the Windows WhatsApp app |
| Any other command | Sends the request to Gemini |

---

## How It Works

1. `voice_handler.record_phrase()` opens the mic and waits for speech, using an
   auto-calibrated noise threshold so a fan isn't mistaken for a word.
2. `speech_recognition` converts the audio into text.
3. KOVA waits for a wake word before accepting a full command, then stays awake
   until you say "go to sleep" or `AWAKE_TIMEOUT` seconds pass.
4. `desktop_agent.Agent.handle()` normalizes the text and matches it against the
   command table. Simple commands run locally; requests with several parts or
   conditions go to the agent instead.
5. The agent loop lets Gemini plan tool calls, checks every call against
   `permissions.json` and the tool's safety level, runs them, and feeds the
   results back — for at most 6 steps.
6. `speak()` writes a temporary `kova_voice.mp3` in the system temp folder,
   plays it, then removes it, falling back to the Windows offline voice.

---

## Customization Ideas

| File/Value | What To Change |
|---|---|
| `USER_TITLE` in `config.json` | Change how KOVA addresses the user |
| `ASSISTANT_NAME` / `WAKE_WORDS` in `config.json` | Rename the assistant and what speech recognition may hear instead |
| `music_library.py` | Add your own songs and links |
| `apps.json` | Add apps KOVA can open, close and restart (with their process names) |
| `permissions.json` | Allow tools without asking, or block them completely |
| `GEMINI_MODEL` / `GEMINI_MODELS` in `config.json` | Switch model, or set the fallback order |
| `BROWSER` in `config.json` | Point at your browser, or `"default"` for the system one |
| `Command` table in `desktop_agent.py` | Add more local automations (first match wins, so specific before general) |
| `plugins/*.py` | Add a tool the AI can call, just by decorating a function with `@tool` |

---

## Platform Notes

- The current browser path is set for Google Chrome on Windows.
- WhatsApp launching uses PowerShell and the Windows app launcher.
- Microphone, gTTS, Google speech recognition, Gemini replies, and news fetching need an active internet connection.

---

## Security Notes

- Keep real API keys inside `config.json` only.
- Do not commit `config.json` or `permissions.json` to GitHub; both are in `.gitignore`.
- `.gitignore` already excludes local config, virtual environments, cache files and `logs/`.
- `config.json.example` and `permissions.json.example` are safe because they contain placeholders only.
- The agent cannot run shell commands or install software; risky tools are `controlled`
  or `critical` and ask before they run, and `blocked` tools never run at all.

---

## Concepts Practiced

| Area | Concepts |
|---|---|
| Python basics | functions, loops, conditionals, imports |
| Voice input | microphone recording and speech-to-text |
| Audio output | generated speech playback |
| APIs | Gemini API and News API requests |
| Config files | JSON-based secret management |
| Automation | opening apps, websites, and saved links |
| Error handling | microphone, speech recognition, and API failure paths |

---

## Future Improvements

- Persist reminders across restarts (Day 3's SQLite memory).
- Make browser/app launching cross-platform.
- Add a setup checker for config keys and microphone access.
- Add more offline commands for quick local tasks.
- Add a small GUI or tray control panel.
- Move the long-running provider and process calls off the voice loop so the
  microphone never goes quiet while waiting for a reply.

---

<p align="center">
  <b>A practical Python voice assistant project built with real speech, automation, and Gemini AI workflows.</b>
</p>

<p align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&color=0:8B5CF6,55:0EA5E9,100:020617&height=100&section=footer" alt="Footer wave" />
</p>
