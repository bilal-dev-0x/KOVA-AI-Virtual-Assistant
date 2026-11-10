import json
import os

from modules.logger import get_logger

# The companion windows: a small always-on-top window that shows what Max is
# doing while the dashboard is in the tray. It is created the first time it is
# needed, never at startup, so a user who keeps the dashboard open pays no
# memory for it. Windows 10 gives frameless windows square corners and
# transparency is unreliable, so the base window is an opaque rectangle with a
# 1px line. Any failure here only means no companion: the tray icon and the
# hotkey still work.
# 10 Nov: the rectangular pill is no longer shown on the desktop — the Mini
# Reaper persona is the only companion. The Pill class stays because Pet is
# built on it (show/hide/push/destroy lifecycle and PillApi are shared).

log = get_logger()

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
WIDTH, HEIGHT = 300, 64
MARGIN = 16
TASKBAR = 48


class PillApi:
    def __init__(self, on_open, on_talk):
        self._on_open = on_open
        self._on_talk = on_talk

    def expand(self):
        self._on_open()

    def talk(self):
        return self._on_talk()


def corner(screen_size, size=(WIDTH, HEIGHT), margin=MARGIN):
    """Top-left of a window that sits in the bottom-right corner, above the taskbar."""
    width, height = screen_size
    return max(0, width - size[0] - margin), max(0, height - size[1] - margin)


def screen_size():
    """Right and bottom edge of the work area: the screen without the taskbar."""
    try:
        import ctypes
        from ctypes import wintypes
        area = wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(area), 0)   # SPI_GETWORKAREA
        if area.right > 0 and area.bottom > 0:
            try:
                scale = ctypes.windll.shcore.GetScaleFactorForDevice(0) / 100   # pywebview sizes are in unscaled units
            except Exception:
                scale = 1
            return int(area.right / (scale or 1)), int(area.bottom / (scale or 1))
        user32 = ctypes.windll.user32
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    except Exception:
        return 1366, 720


def round_window(title, size):
    """Clip the window to a circle (Windows 10 has no reliable transparent window,
    so the companion is cut to its shape instead). Best effort, on its own thread."""
    import ctypes
    import time
    for _ in range(20):
        hwnd = ctypes.windll.user32.FindWindowW(None, title)
        if hwnd:
            from ctypes import wintypes
            rect = wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
            side = rect.right - rect.left   # the real width, whatever the screen scaling
            # The form comes out shorter than asked; make it square before cutting the circle
            ctypes.windll.user32.SetWindowPos(hwnd, 0, rect.left, rect.top, side, side, 0x0014)  # no z-order change, no activate
            region = ctypes.windll.gdi32.CreateEllipticRgn(0, 0, side + 1, side + 1)
            ctypes.windll.user32.SetWindowRgn(hwnd, region, True)
            return True
        time.sleep(0.25)
    return False


class Pill:
    PAGE = "pill.html"
    TITLE = "KOVA"
    SIZE = (WIDTH, HEIGHT)
    ROUND = False

    def __init__(self, on_open, on_talk, create_window=None, size_of_screen=screen_size):
        self._api = PillApi(on_open, on_talk)
        self._create = create_window
        self._screen = size_of_screen
        self._window = None
        self._last_state = None
        self._failed = False

    @property
    def window(self):
        return self._window

    def show(self):
        if self._failed:
            return False
        try:
            if self._window is None:
                x, y = corner(self._screen(), self.SIZE, margin=MARGIN if not self.ROUND else 96)
                create = self._create
                if create is None:
                    import webview
                    create = webview.create_window
                self._window = create(
                    title=self.TITLE, url="file:///" + WEB_DIR.replace("\\", "/") + "/" + self.PAGE,
                    js_api=self._api, width=self.SIZE[0], height=self.SIZE[1], x=x, y=y, frameless=True,
                    easy_drag=True, on_top=True, resizable=False, background_color="#10141d",
                    shadow=False, focus=False)
                if self.ROUND and self._create is None:
                    import threading
                    threading.Thread(target=round_window, args=(self.TITLE, self.SIZE), daemon=True).start()
            else:
                self._window.show()
            self.push(self._last_state)
            return True
        except Exception as e:
            log.warning("The floating pill could not open: %s", e)
            self._failed = True
            self._window = None
            return False

    def hide(self):
        if self._window is not None:
            try:
                self._window.hide()
            except Exception as e:
                log.debug("pill hide skipped: %s", e)

    def push(self, event):
        """Latest state for the pill; kept so a pill shown later starts truthful."""
        if event is None:
            return
        if event.get("t") == "state":
            self._last_state = event
        if self._window is None:
            return
        try:
            self._window.evaluate_js("window.kovaPill && window.kovaPill(%s)" % json.dumps(event))
        except Exception as e:
            log.debug("pill update skipped: %s", e)

    def destroy(self):
        window, self._window = self._window, None
        if window is not None:
            try:
                window.destroy()
            except Exception:
                pass


class Pet(Pill):
    """The Reaper on the desktop all the time: a round, draggable, always-on-top window.
    Double-click opens the dashboard."""
    PAGE = "pet.html"
    TITLE = "KOVA companion"
    SIZE = (200, 200)
    ROUND = True
