import re

from modules.logger import get_logger

# Push-to-talk from anywhere: one global hotkey (PTT_HOTKEY in config.json,
# Ctrl+Alt+M by default) does exactly what the mic button does. pynput's
# listener runs on its own thread and calls back into it, so the callback only
# hands the work to the controller. The listener is never installed by tests.

log = get_logger()
DEFAULT_HOTKEY = "Ctrl+Alt+M"
TYPE_HOTKEY = "Ctrl+Alt+K"   # from anywhere: KOVA in front, the cursor in the message box

# What pynput calls the keys, by the names people type in a settings box
SPECIALS = {
    "ctrl": "ctrl", "control": "ctrl",
    "alt": "alt",
    "shift": "shift",
    "win": "cmd", "windows": "cmd", "cmd": "cmd", "super": "cmd",
    "esc": "esc", "escape": "esc",
    "enter": "enter", "return": "enter",
    "space": "space", "tab": "tab",
    "home": "home", "end": "end", "insert": "insert", "delete": "delete", "del": "delete",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "pageup": "page_up", "pagedown": "page_down",
    "backspace": "backspace", "printscreen": "print_screen", "pause": "pause",
}


def _name(key):
    if len(key) == 1 and key.isalnum():
        return key
    if key in SPECIALS:
        return f"<{SPECIALS[key]}>"
    if re.fullmatch(r"f\d{1,2}", key) and 1 <= int(key[1:]) <= 24:
        return f"<{key}>"
    raise ValueError(f"unknown hotkey key {key!r}")


def parse(spec):
    """"Ctrl+Alt+M" -> "<ctrl>+<alt>+m": what pynput's GlobalHotKeys wants.
    Raises ValueError for a string with no usable key in it."""
    keys = []
    for part in re.split(r"[+-]", spec or ""):
        part = part.strip().lower()
        if part:
            keys.append(_name(part))
    if not keys:
        raise ValueError(f"no hotkey in {spec!r}")
    return "+".join(keys)


class Hotkey:
    """The push-to-talk hotkey, live only while the dashboard runs."""

    def __init__(self, on_press, label="Push-to-talk hotkey"):
        self.on_press = on_press
        self.label = label
        self.spec = None
        self._listener = None

    @property
    def listening(self):
        listener = self._listener
        return listener is not None and listener.is_alive()

    def start(self, spec, backend=None):
        """Take the hotkey. False leaves the old one working: a typo in
        PTT_HOTKEY must not take away the hotkey that already worked."""
        if backend is None:
            try:
                from pynput import keyboard
            except ImportError as e:
                log.warning("No push-to-talk hotkey: %s", e)
                return False
            backend = keyboard
        try:
            listener = backend.GlobalHotKeys({parse(spec): self._fire})
        except Exception as e:  # a key pynput doesn't know, an empty string
            log.warning("Push-to-talk hotkey %r refused: %s", spec, e)
            return False
        old, self._listener = self._listener, listener
        try:
            listener.start()
        except Exception as e:
            self._listener = old
            log.warning("Push-to-talk hotkey %r could not be registered: %s", spec, e)
            return False
        if old is not None:
            old.stop()
        self.spec = spec
        log.info("%s: %s", self.label, spec)
        return True

    def stop(self):
        listener, self._listener = self._listener, None
        self.spec = None
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                log.debug("Hotkey listener already gone")

    def _fire(self):
        try:
            self.on_press()
        except Exception:
            log.exception("Push-to-talk hotkey failed")
