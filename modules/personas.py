import json
import os

from modules.logger import get_logger

# A persona is one small file in personas/: name, voice, wake words, a line of
# manner for the AI prompt, and the tint of the mascot. Tools, approvals and
# honesty are the same for every persona; only wording, voice and looks change.

log = get_logger()

PERSONA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "personas")
DEFAULT = "max"


def load(persona_id):
    """One persona as a dict, or None when the file is missing or broken."""
    persona_id = str(persona_id or "")
    if not persona_id.isalnum():
        return None
    try:
        with open(os.path.join(PERSONA_DIR, persona_id + ".json"), encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("name") or data.get("voice") not in ("male", "female"):
        return None
    words = data.get("wake_words")
    data["wake_words"] = [str(w).lower() for w in words] if isinstance(words, list) and words else [data["name"].lower()]
    data["id"] = persona_id
    return data


def all_ids():
    try:
        names = sorted(n[:-5] for n in os.listdir(PERSONA_DIR) if n.endswith(".json"))
    except OSError:
        return []
    return [n for n in names if load(n)]


def current(config):
    """The persona in effect: PERSONA in config, else the default one."""
    return load(config.get("PERSONA")) or load(DEFAULT)


def tone(config):
    """The manner line for the AI prompt, empty when no persona file is found."""
    persona = current(config)
    return persona["tone"] if persona and persona.get("tone") else ""


def settings_for(persona_id):
    """The config keys a persona sets, or None for an unknown id."""
    persona = load(persona_id)
    if persona is None:
        return None
    return {"PERSONA": persona["id"], "ASSISTANT_NAME": persona["name"],
            "VOICE": persona["voice"], "WAKE_WORDS": persona["wake_words"]}
