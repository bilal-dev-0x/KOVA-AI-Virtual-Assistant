import time

from modules.logger import get_logger

# Gemini is only used when no local command matched.
# The library is imported on the first question, never at startup.

log = get_logger()
_client = None  # must stay referenced, or the SDK closes its connection
_chat = None

ATTEMPTS = 2
PLACEHOLDER_KEYS ={"", "PASTE_YOUR_GEMINI_API_KEY_HERE"}


def is_available(config):
    return config.get("GEMINI_API_KEY", "") not in PLACEHOLDER_KEYS


def _get_chat(config):
    global _client, _chat
    if _chat is None:
        from google import genai
        from google.genai import types

        _client = genai.Client(
            api_key=config["GEMINI_API_KEY"],
            http_options=types.HttpOptions(timeout=60_000)
        )
        title = config.get("USER_TITLE", "Commander")
        _chat = _client.chats.create(
            model=config.get("GEMINI_MODEL", "gemini-3.6-flash"),
            config=types.GenerateContentConfig(system_instruction=(
                f"You are {config.get('ASSISTANT_NAME', 'KOVA')}, a helpful AI desktop assistant on Windows. "
                "Give short, spoken-style responses without markdown. "
                f"Address the user naturally as '{title}'. "
                # Until Day 2 connects Gemini to tools, it can only talk. Without this
                # it happily says "Opening GitHub for you" while nothing happens.
                "IMPORTANT: you cannot control the computer. You cannot open, close, play, "
                "install or change anything, and you cannot see the screen or system status. "
                "Never say or imply that you did an action. If the user asks for an action, "
                "say you can't do that yourself and suggest a command they can say instead, "
                "such as 'open github', 'close chrome', 'play <song>', 'volume up' or 'system status'. "
                "Speech recognition may mishear words, so if a request looks garbled, "
                "briefly ask the user to repeat it."
            ))
        )
    return _chat


def ask(text, config):
    """Ask Gemini. Returns the answer, or None if Gemini is not configured or failed."""
    if not is_available(config):
        return None

    print("Thinking.....")
    for attempt in range(1, ATTEMPTS + 1):
        try:
            response = _get_chat(config).send_message(text)
            break
        except Exception as e:
            log.error("Gemini request failed (attempt %s/%s): %s", attempt, ATTEMPTS, e)
            if attempt == ATTEMPTS:
                return None
            time.sleep(1)

    if response.text:
        return response.text.strip()
    return None


def reset():
    global _client, _chat
    _client = None
    _chat = None
