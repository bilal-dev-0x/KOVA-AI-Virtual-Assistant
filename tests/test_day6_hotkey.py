"""Day 6 step 5a: the push-to-talk hotkey. pynput is faked — a real listener
would install a hook on this PC's keyboard."""
import pytest

from ui import hotkey


class FakeListener:
    def __init__(self, mapping):
        self.mapping = mapping
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def is_alive(self):
        return self.started and not self.stopped


class FakeKeyboard:
    """Stands in for pynput.keyboard: records every listener it was asked for."""

    def __init__(self, refuse=False):
        self.made = []
        self.refuse = refuse

    def GlobalHotKeys(self, mapping):
        listener = FakeListener(mapping)
        if self.refuse:
            def refuse_start():
                raise OSError("hook refused")
            listener.start = refuse_start
        self.made.append(listener)
        return listener


# ---------- the string ----------

def test_a_normal_hotkey_becomes_what_pynput_expects():
    assert hotkey.parse("Ctrl+Alt+M") == "<ctrl>+<alt>+m"
    assert hotkey.parse("ctrl + alt + m") == "<ctrl>+<alt>+m"
    assert hotkey.parse("Ctrl-Alt-M") == "<ctrl>+<alt>+m"


def test_function_keys_and_windows_key():
    assert hotkey.parse("F9") == "<f9>"
    assert hotkey.parse("Win+Shift+S") == "<cmd>+<shift>+s"
    assert hotkey.parse("ctrl+space") == "<ctrl>+<space>"


@pytest.mark.parametrize("bad", ["", "   ", None, "+++", "banana+key"])
def test_a_useless_hotkey_string_is_refused(bad):
    with pytest.raises(ValueError):
        hotkey.parse(bad)


def test_a_single_letter_is_allowed():
    assert hotkey.parse("m") == "m"


# ---------- the listener ----------

def test_start_registers_the_hotkey_and_stopping_releases_it():
    pressed = []
    backend = FakeKeyboard()
    kova = hotkey.Hotkey(lambda: pressed.append(1))

    assert kova.start("Ctrl+Alt+M", backend=backend) is True
    assert kova.listening
    assert kova.spec == "Ctrl+Alt+M"
    assert list(backend.made[0].mapping) == ["<ctrl>+<alt>+m"]

    backend.made[0].mapping["<ctrl>+<alt>+m"]()   # the user presses it
    assert pressed == [1]

    kova.stop()
    assert not kova.listening
    assert kova.spec is None


def test_a_refused_hotkey_keeps_the_one_that_worked():
    backend = FakeKeyboard()
    kova = hotkey.Hotkey(lambda: None)
    assert kova.start("Ctrl+Alt+M", backend=backend) is True
    first = backend.made[0]

    assert kova.start("banana", backend=backend) is False   # typo in the settings
    assert kova.listening and kova.spec == "Ctrl+Alt+M"
    assert first.stopped is False                            # still hooked

    assert kova.start("Ctrl+Alt+J", backend=backend) is True  # a good new one
    assert first.stopped is True                              # the old one let go
    assert kova.spec == "Ctrl+Alt+J"


def test_a_listener_windows_refuses_leaves_the_old_hotkey_in_place():
    backend = FakeKeyboard()
    kova = hotkey.Hotkey(lambda: None)
    assert kova.start("Ctrl+Alt+M", backend=backend) is True
    first = backend.made[0]

    refusing = FakeKeyboard(refuse=True)
    assert kova.start("Ctrl+Alt+J", backend=refusing) is False
    assert kova.listening and kova.spec == "Ctrl+Alt+M"
    assert first.stopped is False


def test_a_broken_callback_does_not_escape_into_pynput():
    def boom():
        raise RuntimeError("controller gone")

    backend = FakeKeyboard()
    kova = hotkey.Hotkey(boom)
    kova.start("Ctrl+Alt+M", backend=backend)
    backend.made[0].mapping["<ctrl>+<alt>+m"]()   # must not raise
    kova.stop()
