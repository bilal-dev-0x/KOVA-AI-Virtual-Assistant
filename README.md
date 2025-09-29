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
