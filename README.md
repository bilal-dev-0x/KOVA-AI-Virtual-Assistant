<p align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&color=0:0F172A,50:2563EB,100:7C3AED&height=170&section=header&text=Jarvis%20AI%20Assistant&fontSize=40&fontColor=ffffff&animation=fadeIn&fontAlignY=36&desc=Voice-controlled%20Python%20assistant%20with%20AI%20replies%20and%20automation%20commands&descSize=14&descAlignY=56" alt="Jarvis AI Assistant banner" />
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.10+" />
  <img src="https://img.shields.io/badge/Type-Voice%20Assistant-7C3AED?style=for-the-badge" alt="Voice Assistant" />
  <img src="https://img.shields.io/badge/OpenAI-API%20Powered-10A37F?style=for-the-badge&logo=openai&logoColor=white" alt="OpenAI API" />
  <img src="https://img.shields.io/badge/License-MIT-22C55E?style=for-the-badge" alt="MIT License" />
</p>

---

## Overview

Jarvis AI Assistant is a Python voice assistant that listens for the wake word `Jarvis`, accepts spoken commands, opens common websites/apps, plays saved songs, reads date/time, fetches news headlines, and sends fallback questions to an AI model.

This is a beginner-to-intermediate Python project focused on speech recognition, text-to-speech, API usage, config-based secrets, and command routing.

---

## Features

| Feature | What It Does |
|---|---|
| Wake word listener | Starts command mode when it hears `Jarvis` |
| Voice commands | Uses microphone input through `speech_recognition` |
| Text-to-speech | Speaks responses with `gTTS` and `pygame` |
| AI replies | Sends unknown commands to the OpenAI API |
| Web shortcuts | Opens YouTube, Google, Spotify, and WhatsApp |
| Music library | Plays saved music links from `music_library.py` |
| Date and time | Reads the current date and time aloud |
| News support | Fetches top headlines when a News API key is configured |

---

## Project Structure

```text
Jarvis-AI-Virtual-Assistant/
|-- main.py
|-- music_library.py
|-- config.json.example
|-- requirements.txt
|-- LICENSE
`-- README.md
```

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

### 4. Add API keys

Copy the example config:

```bash
copy config.json.example config.json
```

Then edit `config.json`:

```json
{
  "OPENAI_API_KEY": "YOUR_OPENAI_KEY_HERE",
  "NEWS_API_KEY": "YOUR_NEWS_API_KEY_HERE"
}
```

`NEWS_API_KEY` is optional, but AI replies require `OPENAI_API_KEY`.

---

## Run

```bash
python main.py
```

Say the wake word:

```text
Jarvis
```

Then try commands like:

```text
open youtube
open google
open spotify
play <song name>
today date
current time
global news
open whatsapp
```

---

## Security Notes

- Keep real API keys inside `config.json` only.
- Do not commit `config.json` to GitHub.
- `config.json.example` is safe because it contains placeholders only.

---

## Concepts Practiced

| Area | Concepts |
|---|---|
| Python basics | functions, loops, conditionals, imports |
| Voice input | microphone listening and speech-to-text |
| Audio output | generated speech playback |
| APIs | OpenAI API and News API requests |
| Config files | JSON-based secret management |
| Automation | opening apps, websites, and saved links |

---

## Future Improvements

- Add a cleaner command router.
- Add better error handling for microphone and API failures.
- Add more app shortcuts.
- Add offline command mode.
- Convert repeated commands into reusable functions.

---

<p align="center">
  <b>A practical Python assistant project built while learning real automation and AI API workflows.</b>
</p>

<p align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&color=0:7C3AED,50:2563EB,100:0F172A&height=100&section=footer" alt="Footer wave" />
</p>
