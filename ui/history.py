"""Small, local conversation history for the dashboard Assistant screen."""

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from modules.memory_engine import looks_secret

BASE_DIR = Path(__file__).resolve().parent.parent
HISTORY_FILE = BASE_DIR / "data" / "history.jsonl"
KEEP = 500

_lock = threading.Lock()


def _read(path):
    messages = []
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    item = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if (isinstance(item, dict) and item.get("role") in ("user", "max")
                        and isinstance(item.get("text"), str) and item.get("id")):
                    messages.append(item)
    except FileNotFoundError:
        pass
    return messages[-KEEP:]


def all_messages():
    """Return the last 500 saved chat messages, newest at the end."""
    with _lock:
        return _read(Path(HISTORY_FILE))


def append(role, text):
    """Save a chat line locally, unless it resembles a password or credential.

    The file is replaced atomically so a shutdown during a write cannot leave a
    half-written history. The API key never passes through logs or the page API.
    """
    if role not in ("user", "max") or not isinstance(text, str) or not text.strip():
        return None
    if looks_secret(text):
        return None

    path = Path(HISTORY_FILE)
    with _lock:
        messages = _read(path)
        item = {"id": uuid.uuid4().hex,
                "role": role,
                "text": text[:12000],
                "time": datetime.now(timezone.utc).isoformat()}
        messages.append(item)
        messages = messages[-KEEP:]
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n"
                                                for row in messages), encoding="utf-8")
        temporary.replace(path)
        return item
