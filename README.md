<p align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&color=0:020617,45:0EA5E9,100:8B5CF6&height=175&section=header&text=Jarvis%20AI%20Virtual%20Assistant&fontSize=38&fontColor=ffffff&animation=fadeIn&fontAlignY=36&desc=Voice%20commands%20%7C%20Gemini%20AI%20replies%20%7C%20News%20and%20app%20automation&descSize=14&descAlignY=56" alt="Jarvis AI Virtual Assistant banner" />
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.10+" />
  <img src="https://img.shields.io/badge/Type-Voice%20Assistant-0EA5E9?style=for-the-badge" alt="Voice Assistant" />
  <img src="https://img.shields.io/badge/Gemini-API%20Powered-8B5CF6?style=for-the-badge&logo=googlegemini&logoColor=white" alt="Gemini API" />
  <img src="https://img.shields.io/badge/License-MIT-22C55E?style=for-the-badge" alt="MIT License" />
</p>

---

## Overview

Jarvis AI Virtual Assistant is a Python-based voice assistant that listens for the wake word `Jarvis`, records microphone input with `sounddevice`, converts speech to text, responds out loud, opens common websites/apps, plays saved music links, reads date/time, fetches news headlines, and sends unknown commands to Google Gemini for short AI replies.

The latest version is configured around Gemini, supports a configurable Gemini model through `config.json`, and addresses the user naturally as `Commander`.

---

## Feature Highlights

| Feature | Current Behavior |
|---|---|
| Wake word mode | Starts command mode when it hears `Jarvis` |
| Microphone input | Records short audio clips with `sounddevice` |
| Speech recognition | Uses `speech_recognition` with Google recognition |
| Spoken replies | Converts responses to speech with `gTTS` and plays them with `pygame` |
| Gemini fallback | Sends unknown commands to Gemini through `google-genai` |
| Personalized tone | Addresses the user as `Commander` using `USER_TITLE` |
| Web shortcuts | Opens YouTube, Google, and Spotify in Chrome |
| WhatsApp launcher | Opens the Windows WhatsApp app through PowerShell |
| Music library | Plays saved links from `music_library.py` |
| News headlines | Reads top Pakistan headlines when `NEWS_API_KEY` is configured |
| Date and time | Speaks the current date and current time |

---

## Project Structure

```text
Jarvis-AI-Virtual-Assistant/
|-- main.py
|-- music_library.py
|-- config.json.example
|-- requirements.txt
|-- .gitignore
|-- LICENSE
`-- README.md
```

---

## Tech Stack

| Tool | Purpose |
|---|---|
| Python | Main programming language |
| google-genai | Gemini AI responses |
| SpeechRecognition | Speech-to-text recognition |
| sounddevice | Microphone recording |
| gTTS | Text-to-speech generation |
| pygame-ce | Audio playback |
| requests | News API requests |
| webbrowser/subprocess | Website and app automation |

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
  "NEWS_API_KEY": ""
}
```

`GEMINI_API_KEY` is required for AI replies. `NEWS_API_KEY` is optional and only needed for the `global news` command.

---

## Run

```bash
python main.py
```

First say the wake word:

```text
Jarvis
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

1. `listen_with_sounddevice()` records a short microphone clip.
2. `speech_recognition` converts the audio into text.
3. Jarvis waits for the wake word before accepting a full command.
4. Built-in commands handle websites, music, date/time, news, and WhatsApp.
5. Unknown commands are passed to Gemini using the configured model.
6. `speak()` creates a temporary `jarvis_voice.mp3`, plays it, then removes it.

---

## Customization Ideas

| File/Value | What To Change |
|---|---|
| `USER_TITLE = "Commander"` | Change how Jarvis addresses the user |
| `music_library.py` | Add your own songs and links |
| `GEMINI_MODEL` | Switch Gemini model from config |
| `chrome_path` in `main.py` | Adjust Chrome path for your machine |
| Command `elif` blocks | Add more local automations |

---

## Platform Notes

- The current browser path is set for Google Chrome on Windows.
- WhatsApp launching uses PowerShell and the Windows app launcher.
- Microphone, gTTS, Google speech recognition, Gemini replies, and news fetching need an active internet connection.

---

## Security Notes

- Keep real API keys inside `config.json` only.
- Do not commit `config.json` to GitHub.
- `.gitignore` already excludes local config, virtual environments, cache files, and generated audio.
- `config.json.example` is safe because it contains placeholders only.

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

- Move commands into a cleaner command router.
- Make browser/app launching cross-platform.
- Add a setup checker for config keys and microphone access.
- Add tests with mocked speech input and API responses.
- Add more offline commands for quick local tasks.
- Add a small GUI or tray control panel.

---

<p align="center">
  <b>A practical Python voice assistant project built with real speech, automation, and Gemini AI workflows.</b>
</p>

<p align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&color=0:8B5CF6,55:0EA5E9,100:020617&height=100&section=footer" alt="Footer wave" />
</p>
