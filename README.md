
![GitHub repo size](https://img.shields.io/github/repo-size/bilal-dev-0x/Jarvis-AI-Virtual-Assistant?style=flat-square)
![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)
![Python Version](https://img.shields.io/badge/Python-3.10+-blue?style=flat-square)
![Last Commit](https://img.shields.io/github/last-commit/bilal-dev-0x/Jarvis-AI-Virtual-Assistant?style=flat-square)
![Issues](https://img.shields.io/github/issues/bilal-dev-0x/Jarvis-AI-Virtual-Assistant?style=flat-square)

# Jarvis AI Assistant (Config-based Release)

This repository contains a GitHub-ready version of the **Jarvis AI Assistant**.
Sensitive credentials (API keys) have been removed for security.

## Setup

1. Copy `config.json.example` to `config.json`.
2. Paste your OpenAI API key (and optional News API key) in `config.json`.
3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Run Jarvis:

```bash
python main.py
```

## Notes

- Users do not need `.env` or environment variables.
- Update your music library in `music_library.py`.
- API keys should **never** be committed to GitHub.

## Instructions to use

# Jarvis AI Assistant - User Instructions

Welcome to Jarvis AI Assistant! This guide will help you set up and run Jarvis easily.

---

## 1. Prerequisites

- Python 3.10 or higher
- Internet connection
- Microphone for voice commands

---

## 2. Installation Steps

1. **Download the project** or clone it:

```bash
git clone https://github.com/bilal-dev-0x/JarvisAI.git
cd JarvisAI
```

2. **Create a virtual environment** (recommended):

```bash
python -m venv .venv
```

3. **Activate the virtual environment**:

- Windows (PowerShell):
```powershell
& ".\.venv\Scripts\Activate.ps1"
```

- Linux/macOS:
```bash
source .venv/bin/activate
```

4. **Install dependencies**:

```bash
pip install -r requirements.txt
```

---

## 3. Configure API Keys

1. Copy `config.json.example` → rename to `config.json`.
2. Paste your keys into `config.json`:

```json
{
    "OPENAI_API_KEY": "YOUR_OPENAI_KEY_HERE",
    "NEWS_API_KEY": "YOUR_NEWS_API_KEY_HERE"
}
```

> ⚠️ Never share your API keys publicly.

---

## 4. Running Jarvis

1. Activate your virtual environment.
2. Start Jarvis:

```bash
python main.py
```

3. Speak the wake word: **“Jarvis”**.
4. Supported commands:

### Web / Apps
- `Open YouTube`
- `Open Spotify`
- `Open Facebook`
- `Open Google`
- `Open WhatsApp` (Windows only)

### Music Library
- `Play <song name>` (if it exists in `music_library.py`)

### General
- `Hello`
- `Today date`
- `Current time`
- `Global news`
- `Pakistani news`

> ⚠️ Commands **not included yet** (like `Open LinkedIn`) will not work unless added in `main.py`.

---

## 5. Customization

- Add more songs in `music_library.py`
- Add new commands in `main.py`
- Extend AI responses using OpenAI GPT API

---

## 6. Troubleshooting

- Check microphone settings if Jarvis cannot hear you
- Verify API keys in `config.json`
- Reinstall dependencies: `pip install -r requirements.txt`
