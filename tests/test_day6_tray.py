"""Day 6 step 5b: the tray icon. pystray is faked — a real icon would appear
in this PC's notification area."""
import sys
import threading
import time
from types import SimpleNamespace

from ui import tray


class FakeIcon:
    """Stands in for pystray.Icon: run() blocks like the real one until stop()."""

    def __init__(self, name, image, title, menu=None):
        self.name, self.image, self.title, self.menu = name, image, title, menu
        self.stopped = False
        self._done = threading.Event()

    def run(self):
        self._done.wait(timeout=5)

    def stop(self):
        self.stopped = True
        self._done.set()


class FakePystray:
    def __init__(self):
        self.icons = []

    def Menu(self, *items):
        return list(items)

    def MenuItem(self, title, action, **options):
        return SimpleNamespace(title=title, action=action, **options)

    def Icon(self, name, image, title, menu=None):
        icon = FakeIcon(name, image, title, menu)
        self.icons.append(icon)
        return icon


def make(visible=True, muted=False):
    calls = []
    state = {"visible": visible, "muted": muted}
    kova = tray.Tray(
        on_show=lambda: (calls.append("show"), state.update(visible=True)),
        on_hide=lambda: (calls.append("hide"), state.update(visible=False)),
        on_mute=lambda: (calls.append("mute"), state.update(muted=not state["muted"])),
        on_quit=lambda: calls.append("quit"),
        is_visible=lambda: state["visible"],
        is_muted=lambda: state["muted"],
    )
    return kova, calls, state


def wait_for(condition, timeout=5):
    end = time.time() + timeout
    while not condition():
        if time.time() > end:
            raise AssertionError("timed out")
        time.sleep(0.005)


def test_the_icon_is_drawn_in_code_with_no_image_file():
    image = tray.icon_image()
    assert image.size == (64, 64) and image.mode == "RGB"
    # More than one colour: the background and the letter
    assert len(image.getcolors(maxcolors=100_000) or []) > 1


def test_start_shows_one_icon_with_the_three_menu_items():
    backend = FakePystray()
    kova, _, _ = make()

    assert kova.start(backend=backend) is True
    wait_for(lambda: kova.running)
    assert len(backend.icons) == 1
    icon = backend.icons[0]
    assert icon.name == "kova_max"
    assert len(icon.menu) == 3

    kova.stop()
    wait_for(lambda: not kova.running)
    assert icon.stopped


def test_the_labels_tell_the_truth_about_the_window_and_the_mic():
    backend = FakePystray()
    kova, _, _ = make(visible=True, muted=False)
    kova.start(backend=backend)
    menu = backend.icons[0].menu
    # pystray calls a text callable with the menu item, so the title must take it
    assert menu[0].title(menu[0]) == "Hide dashboard"
    assert menu[1].title(menu[0]) == "Mute mic"

    kova.stop()
    backend2 = FakePystray()
    hidden, _, _ = make(visible=False, muted=True)
    hidden.start(backend=backend2)
    menu = backend2.icons[0].menu
    assert menu[0].title(menu[0]) == "Show dashboard"
    assert menu[1].title(menu[0]) == "Unmute mic"
    hidden.stop()


def test_double_clicking_the_icon_shows_or_hides_the_window():
    kova, calls, state = make(visible=True)
    backend = FakePystray()
    kova.start(backend=backend)
    default_item = next(item for item in backend.icons[0].menu if item.default)

    default_item.action()
    assert calls == ["hide"] and state["visible"] is False

    default_item.action()
    assert calls == ["hide", "show"] and state["visible"] is True
    kova.stop()


def test_mute_and_quit_menu_items_call_back():
    kova, calls, _ = make(muted=False)
    backend = FakePystray()
    kova.start(backend=backend)
    mute, quit_item = backend.icons[0].menu[1], backend.icons[0].menu[2]

    mute.action()
    quit_item.action()
    assert calls == ["mute", "quit"]
    kova.stop()


def test_without_pystray_the_dashboard_still_starts(monkeypatch):
    monkeypatch.setitem(sys.modules, "pystray", None)   # import pystray -> ImportError
    kova, calls, _ = make()
    assert kova.start() is False
    assert calls == []
