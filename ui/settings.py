from modules.logger import get_logger

# The Settings tab's logic, kept out of the widgets so it can be tested without
# a window. Saving goes through tools.save_setting, so every other key in
# config.json stays exactly as it is and nothing is ever printed: the file holds
# API keys, and Day 6 only ever writes the four keys below.

log = get_logger()

VOICES = ("male", "female")
DEFAULTS = {"VOICE": "female", "ALERTS_ON": False, "PTT_HOTKEY": "Ctrl+Alt+M"}


def parse_wake_words(text):
    """"Max, mac , max" -> ["max", "mac"]: lower-cased, de-duplicated, order kept."""
    words = []
    for word in (text or "").split(","):
        word = word.strip().lower()
        if word and word not in words:
            words.append(word)
    return words


def _wake_words(config):
    """The wake words in effect right now: the same rule Agent uses at startup,
    so a box that still shows them is not a change."""
    from desktop_agent import wake_words_from
    return wake_words_from(config)


def changes(current, form):
    """Only the settings that really differ from what is loaded.

    A save that changes nothing must not rewrite config.json or restart the
    alerts watcher, and a wake word box left as it was must not wipe the list.
    """
    wanted = {}

    voice = form.get("VOICE")
    if voice in VOICES and voice != current.get("VOICE", DEFAULTS["VOICE"]):
        wanted["VOICE"] = voice

    if form.get("ALERTS_ON") is not None and bool(form["ALERTS_ON"]) != bool(current.get("ALERTS_ON", False)):
        wanted["ALERTS_ON"] = bool(form["ALERTS_ON"])

    words = parse_wake_words(form.get("WAKE_WORDS"))
    if words and words != _wake_words(current):
        wanted["WAKE_WORDS"] = words

    hotkey = (form.get("PTT_HOTKEY") or "").strip()
    if hotkey and hotkey != current.get("PTT_HOTKEY", DEFAULTS["PTT_HOTKEY"]):
        wanted["PTT_HOTKEY"] = hotkey

    return wanted


def save(changed):
    """Write the changed keys; tools.save_setting keeps the rest of the file."""
    from modules import tools
    for key, value in changed.items():
        tools.save_setting(key, value)


def apply(agent, changed, on_hotkey=None):
    """Make saved settings count now, so Max doesn't need a restart.

    on_hotkey(spec) re-registers the push-to-talk hotkey; when it isn't given
    (or refuses the new string) the old hotkey keeps working.
    """
    for key, value in changed.items():
        agent.config[key] = value

    if "VOICE" in changed:
        from modules import voice_handler
        voice_handler.set_voice(changed["VOICE"])

    if "ALERTS_ON" in changed:
        from modules import alerts
        if changed["ALERTS_ON"]:
            alerts.start(agent.config)
        else:
            alerts.stop()

    if "WAKE_WORDS" in changed:
        from desktop_agent import wake_words_from
        # The command table embeds the wake words, so it is rebuilt from scratch
        agent.wake_words = wake_words_from(agent.config)
        agent.commands = agent._build_commands()

    if "PTT_HOTKEY" in changed and on_hotkey:
        on_hotkey(changed["PTT_HOTKEY"])


def save_and_apply(agent, form, on_hotkey=None):
    """What the Save button does. Returns the keys that changed (empty = nothing to do)."""
    changed = changes(agent.config, form)
    if not changed:
        return {}
    save(changed)
    apply(agent, changed, on_hotkey)
    log.info("Settings saved: %s", ", ".join(changed))
    return changed
